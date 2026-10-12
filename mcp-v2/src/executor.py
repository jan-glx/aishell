"""aishell v2 privileged executor, independent of the original HTTP API.

The *only* control ingress is a Unix-domain socket. Peers must be an allowed
local Linux UID AND present an OAuth access token for an administrator-mapped
subject. Unix identities come from root-owned configuration, never tool args.

One loopback HTTP listener serves time-limited HMAC-signed download URLs; this
route is NOT installed into production nginx until a separate cutover.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import http.server
import ipaddress
import json
import logging
import mimetypes
import os
import pwd
import grp
import re
import secrets
import selectors
import signal
import socket
import socketserver
import sqlite3
import ssl
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlencode

LOG = logging.getLogger('aishell_v2')
OPERATIONS = {
    'runCommand': 'aishell:control',
    'read_tmux_buffer': 'aishell:control',
    'send_tmux_keys': 'aishell:control',
    'transferFile': 'aishell:files',
    'transferTextFile': 'aishell:files',
    'getFile': 'aishell:files',
}
MAX_FRAME = 100_000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_COMMAND_BYTES = 12_000
MAX_COMMAND_SECONDS = 65
MAX_STDOUT_BYTES = 70_000
SAFE_SESSION = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,48}$')


@dataclass(frozen=True)
class Identity:
    subject: str
    username: str
    uid: int
    gid: int
    home: Path
    scopes: frozenset[str]
    admin: bool


def safe_filename(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 180 or
            value in ('.', '..') or '/' in value or '\\' in value or
            any(ord(c) < 32 for c in value)):
        raise ValueError('Invalid filename: use a basename without separators')
    return value


def safe_session(value: str) -> str:
    if not isinstance(value, str) or not SAFE_SESSION.fullmatch(value):
        raise ValueError('Invalid session name')
    return value


def strict_bearer(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,256}', value):
        raise PermissionError('Missing or invalid access token')
    return value


class Configuration:
    def __init__(self, filename: str):
        p = Path(filename)
        if p.stat().st_uid != 0 or (p.stat().st_mode & 0o022):
            raise PermissionError('Executor configuration must be root-owned, not group/world writable')
        self.path = p
        data = json.loads(p.read_text())
        self.issuer = data['issuer'].rstrip('/')
        self.resource = data['resource']
        self.oauth_db = Path(data['oauth_database'])
        self.socket_path = Path(data['socket_path'])
        self.http_host = data.get('download_listen_host', '127.0.0.1')
        self.http_port = int(data.get('download_listen_port', 8780))
        self.public_download_prefix = data['public_download_prefix'].rstrip('/')
        secret_file = Path(data['download_signing_key'])
        if secret_file.stat().st_uid != 0 or secret_file.stat().st_mode & 0o077:
            raise PermissionError('Download signing secret must be root-owned mode 0600')
        self.signing_secret = secret_file.read_bytes().strip()
        if len(self.signing_secret) < 32:
            raise ValueError('Download signing secret is too short')
        self.socket_group = data['socket_group']
        self.permitted_uids = frozenset(pwd.getpwnam(username).pw_uid
                                       for username in data['permitted_socket_users'])
        self.identities = {}
        for subject, row in data['principals'].items():
            entry = pwd.getpwnam(row['unix_user'])
            if entry.pw_uid == 0 and not row.get('allow_root', False):
                raise ValueError('Root UID must be explicitly approved')
            if entry.pw_uid != 0 and row.get('allow_root', False):
                raise ValueError('allow_root requires root UID')
            home = Path(entry.pw_dir)
            if not home.is_absolute() or not home.is_dir():
                raise ValueError(f'Unix home directory missing for {entry.pw_name}')
            scopes = frozenset(row.get('allowed_scopes', []))
            if not scopes.issubset(set(OPERATIONS.values())):
                raise ValueError('Unknown configured scope')
            self.identities[subject] = Identity(subject, entry.pw_name,
                entry.pw_uid, entry.pw_gid, home, scopes, entry.pw_uid == 0)
        if self.http_host not in ('127.0.0.1', '::1'):
            raise ValueError('Downloads may bind only to loopback')
        if not self.identities:
            raise ValueError('At least one configured principal is required')
        if not self.socket_path.is_absolute():
            raise ValueError('Unix socket path must be absolute')

    def validate(self, bearer: str, operation: str) -> Identity:
        token = strict_bearer(bearer)
        scope = OPERATIONS.get(operation)
        if not scope:
            raise PermissionError('Unknown tool')
        digest = hashlib.sha256(token.encode('ascii')).hexdigest()
        # The privileged process only READS the OAuth database. It never
        # registers OAuth clients, changes access rights, or issues tokens.
        try:
            with sqlite3.connect(f'file:{self.oauth_db}?mode=ro', uri=True, timeout=5) as con:
                row = con.execute('SELECT data,expires FROM tokens WHERE id_hash=? AND kind=?',
                                  (digest, 'access')).fetchone()
        except (sqlite3.Error, OSError) as exc:
            LOG.error('OAuth database unavailable: %s', type(exc).__name__)
            raise PermissionError('OAuth validation unavailable') from None
        if not row or float(row[1]) <= time.time():
            raise PermissionError('Expired or revoked OAuth token')
        metadata = json.loads(row[0])
        if metadata.get('resource') != self.resource:
            raise PermissionError('Wrong OAuth resource')
        # Some historical tokens do not carry an explicit issuer in the DB.
        if metadata.get('issuer', self.issuer) != self.issuer:
            raise PermissionError('Wrong OAuth issuer')
        subject = metadata.get('subject')
        identity = self.identities.get(subject)
        if identity is None:
            raise PermissionError('Unrecognized OAuth subject')
        if scope not in metadata.get('scopes', []) or scope not in identity.scopes:
            raise PermissionError('Scope not granted to subject and Unix account')
        return identity


def user_env(user: Identity) -> dict[str, str]:
    return {'HOME': str(user.home), 'USER': user.username, 'LOGNAME': user.username,
            'SHELL': '/bin/bash', 'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            'LANG': 'C.UTF-8', 'TERM': 'xterm-256color'}


def popen_as(user: Identity, argv: list[str], **kw) -> subprocess.Popen:
    return subprocess.Popen(argv, user=user.uid, group=user.gid, extra_groups=[],
                            cwd=str(user.home), env=user_env(user),
                            close_fds=True, start_new_session=True, **kw)


def run_as(user: Identity, argv: list[str], timeout: float = 12) -> subprocess.CompletedProcess:
    with popen_as(user, argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise TimeoutError('tmux command exceeded time limit') from None
        if len(out) > MAX_STDOUT_BYTES or len(err) > MAX_STDOUT_BYTES:
            raise ValueError('tmux output too large')
        return subprocess.CompletedProcess(argv, proc.returncode, out.decode('utf-8','replace'),
                                           err.decode('utf-8','replace'))


def execute_command(user: Identity, args: dict) -> dict:
    command = args.get('command')
    if (not isinstance(command, str) or not command.strip()
            or len(command.encode('utf-8')) > MAX_COMMAND_BYTES):
        raise ValueError('Command must be nonempty UTF-8 text <= 12 KB')
    with popen_as(user, ['/bin/bash', '-l', '-c', command],
                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE, bufsize=0) as proc:
        chunks = {'stdout': bytearray(), 'stderr': bytearray()}
        streams = {'stdout': proc.stdout, 'stderr': proc.stderr}
        timed_out = False
        truncated = False
        deadline = time.monotonic() + MAX_COMMAND_SECONDS
        with selectors.DefaultSelector() as sel:
            for key, stream in streams.items():
                os.set_blocking(stream.fileno(), False)
                sel.register(stream, selectors.EVENT_READ, key)
            while sel.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in sel.select(timeout=min(remaining, 1.0)):
                    try:
                        buf = os.read(key.fileobj.fileno(), 8192)
                    except BlockingIOError:
                        continue
                    if not buf:
                        sel.unregister(key.fileobj)
                        continue
                    target = chunks[key.data]
                    if len(target) + len(buf) > MAX_STDOUT_BYTES:
                        target.extend(buf[:max(0, MAX_STDOUT_BYTES-len(target))])
                        truncated = True
                        break
                    target.extend(buf)
                if truncated:
                    break
        if timed_out or truncated:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait(timeout=5)
        return {'stdout': chunks['stdout'].decode('utf-8', 'replace'),
                'stderr': chunks['stderr'].decode('utf-8', 'replace'),
                'exit_code': 124 if timed_out else 125 if truncated else proc.returncode,
                'timed_out': timed_out, 'truncated': truncated,
                'unix_user': user.username}


def tmux_command(user: Identity, session: str, keys: list[str] | None = None) -> dict:
    name = safe_session(session)
    tmux = ['tmux', '-L', 'aishell-v2']
    check = run_as(user, tmux + ['has-session', '-t', '=' + name])
    if check.returncode != 0:
        created = run_as(user, tmux + ['new-session', '-d', '-s', name])
        if created.returncode != 0:
            raise RuntimeError('Could not create user tmux session: ' + created.stderr[:200])
    if keys is not None:
        if (not isinstance(keys, list) or not 1 <= len(keys) <= 48
                or any(not isinstance(k, str) or not 1 <= len(k) <= 1024 for k in keys)):
            raise ValueError('Expected 1–48 tmux key strings, each <= 1024 characters')
        sent = run_as(user, tmux + ['send-keys', '-t', '=' + name + ':0.0', *keys])
        if sent.returncode:
            raise RuntimeError('Unable to send tmux keys')
        time.sleep(0.3)
    capture = run_as(user, tmux + ['capture-pane', '-p', '-t', '=' + name + ':0.0'])
    if capture.returncode:
        raise RuntimeError('Unable to capture tmux pane')
    return {'output': capture.stdout, 'unix_user': user.username, 'session': name}


def directory_fd(user: Identity, dirname: str, create: bool) -> int:
    """Open user-owned input/output, refusing symlinks and alien ownership."""
    if dirname not in ('input', 'output'):
        raise ValueError('Invalid user storage directory')
    if create:
        # Caller runs as root, but target directory must be exclusively owned
        # by the mapped Unix user, or deliberately by root for owner mode.
        try:
            os.mkdir(user.home / dirname, 0o700)
            os.chown(user.home / dirname, user.uid, user.gid)
        except FileExistsError:
            pass
    home_fd = os.open(user.home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(dirname, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
    finally:
        os.close(home_fd)
    st = os.fstat(fd)
    if st.st_uid != user.uid or st.st_mode & 0o002:
        os.close(fd)
        raise PermissionError('Unsafe user storage permissions')
    return fd


def save_file(user: Identity, filename: str, content: bytes) -> dict:
    safe_filename(filename)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError('File exceeds 16 MiB limit')
    dfd = directory_fd(user, 'input', create=True)
    # Atomic replace rather than following existing symlinks. Files are saved
    # as the actual Unix user, so disk quotas and permissions apply naturally.
    tmp = '.aishell-v2-' + secrets.token_hex(12)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
        try:
            os.fchown(fd, user.uid, user.gid)
            with os.fdopen(fd, 'wb') as out:
                out.write(content)
        except BaseException:
            try: os.unlink(tmp, dir_fd=dfd)
            except OSError: pass
            raise
        os.replace(tmp, filename, src_dir_fd=dfd, dst_dir_fd=dfd)
    finally:
        os.close(dfd)
    return {'filename': filename, 'path': '~/input/' + filename, 'unix_user': user.username,
            'bytes_written': len(content)}


def fetch_openai_file(url: str) -> bytes:
    """Fetch a user-provided file from allowlisted TLS OpenAI hosts, pinned IP.

    No redirects, private address connections, HTTP or arbitrary hosts. The
    resolved IP is pinned to the TCP connection to prevent DNS rebinding.
    """
    if not isinstance(url, str) or len(url) > 3500:
        raise ValueError('Invalid OpenAI file URL')
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    allowed = host in {'files.oaiusercontent.com', 'api.openai.com', 'files.openai.com'} or host.endswith('.oaiusercontent.com')
    if (parsed.scheme != 'https' or not allowed or parsed.username or parsed.password or
            parsed.port not in (None, 443) or parsed.fragment):
        raise PermissionError('Only HTTPS URLs from OpenAI file-service hosts are allowed')
    addrs = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = []
    for _, _, _, _, endpoint in addrs:
        ip = ipaddress.ip_address(endpoint[0])
        if not ip.is_global:
            raise PermissionError('File URL DNS points to a private or reserved address')
        ips.append(endpoint[0])
    if not ips:
        raise ValueError('No allowed address for file host')
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            raw = socket.create_connection((ips[0], 443), timeout=self.timeout)
            self.sock = self._context.wrap_socket(raw, server_hostname=host)
    conn = PinnedHTTPS(host, port=443, timeout=25, context=ssl.create_default_context())
    try:
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        conn.request('GET', path, headers={'Host': host, 'User-Agent': 'aishell-v2/1.0'})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError('File service HTTP ' + str(response.status) + ' (redirects not followed)')
        size = response.getheader('Content-Length', '')
        if size.isdigit() and int(size) > MAX_FILE_BYTES:
            raise ValueError('OpenAI file too large')
        data = response.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError('OpenAI file too large')
        return data
    finally:
        conn.close()


def signed_download(config: Configuration, user: Identity, filename: str) -> dict:
    safe_filename(filename)
    dfd = directory_fd(user, 'output', create=False)
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_DOWNLOAD_BYTES:
                raise ValueError('Output is not a regular file <=64 MiB')
        finally:
            os.close(fd)
    finally:
        os.close(dfd)
    expires = int(time.time()) + 300
    signature = hmac.new(config.signing_secret,
                         f'{user.subject}\n{filename}\n{expires}'.encode('utf-8'),
                         hashlib.sha256).hexdigest()
    url = (f'{config.public_download_prefix}/{quote(user.subject,safe="")}/'
           f'{quote(filename,safe="")}?{urlencode({"expires":expires,"signature":signature})}')
    return {'openaiFileResponse': [url], 'expires_in_seconds': 300,
            'filename': filename, 'unix_user': user.username}


def dispatch(config: Configuration, user: Identity, op: str, arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError('Tool arguments must be an object')
    if op == 'runCommand':
        return execute_command(user, arguments)
    if op == 'read_tmux_buffer':
        return tmux_command(user, arguments.get('session', 'default'))
    if op == 'send_tmux_keys':
        return tmux_command(user, arguments.get('session', 'default'), arguments.get('input'))
    if op == 'transferTextFile':
        content = arguments.get('content')
        if not isinstance(content, str) or len(content.encode('utf-8')) > 24_000:
            raise ValueError('Text content must be <= 24 KB')
        return save_file(user, arguments.get('filename'), content.encode('utf-8'))
    if op == 'transferFile':
        refs = arguments.get('openaiFileIdRefs')
        if not isinstance(refs, list) or not 1 <= len(refs) <= 6:
            raise ValueError('Expected 1–6 OpenAI file references')
        result = []
        for ref in refs:
            if not isinstance(ref, dict):
                raise ValueError('Invalid file reference')
            name = safe_filename(ref.get('name'))
            payload = fetch_openai_file(ref.get('download_link'))
            result.append(save_file(user, name, payload))
        return {'files': result}
    if op == 'getFile':
        return signed_download(config, user, arguments.get('filename'))
    raise PermissionError('Unknown operation')


class ExecutorHandler(socketserver.StreamRequestHandler):
    def handle(self):
        config = self.server.config
        uid = struct_ucred_uid(self.request)
        if uid not in config.permitted_uids:
            LOG.warning('Denied private executor socket connection (UID=%d)', uid)
            return
        self.request.settimeout(80)
        try:
            frame = self.rfile.readline(MAX_FRAME + 2)
            if not frame.endswith(b'\n') or len(frame) > MAX_FRAME:
                raise ValueError('Invalid or too-large executor frame')
            request = json.loads(frame)
            op = request.get('op')
            identity = config.validate(request.get('access_token'), op)
            LOG.info('Authorized operation=%s principal=%s unix_user=%s', op, identity.subject,
                     identity.username)
            result = {'ok': True, 'result': dispatch(config, identity, op, request.get('arguments', {}))}
        except Exception as exc:
            LOG.warning('Operation denied/failed: %s', type(exc).__name__)
            result = {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:350]}
        packed = json.dumps(result, ensure_ascii=False).encode('utf-8') + b'\n'
        if len(packed) > 185_000:
            packed = b'{"ok":false,"error":"Result too large"}\n'
        self.wfile.write(packed)


def struct_ucred_uid(sock: socket.socket) -> int:
    import struct
    pid, uid, gid = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    return uid


class ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 12


class DownloadHandler(http.server.BaseHTTPRequestHandler):
    server_version = 'aishell-v2-download/1.0'
    sys_version = ''
    def log_message(self, fmt, *args):
        # No URL logging: signatures are bearer download credentials.
        LOG.info('Signed download HTTP status: %s', str(args[1]) if len(args)>1 else '?')

    def do_HEAD(self):
        self.serve_download(with_body=False)

    def do_GET(self):
        self.serve_download(with_body=True)

    def serve_download(self, with_body: bool):
        cfg = self.server.config
        try:
            parsed = urlsplit(self.path)
            prefix = '/mcp-v2/download/'
            if not parsed.path.startswith(prefix):
                raise FileNotFoundError('Unknown download path')
            parts = parsed.path[len(prefix):].split('/')
            if len(parts) != 2:
                raise ValueError('Invalid download path')
            subject, filename = unquote(parts[0]), safe_filename(unquote(parts[1]))
            user = cfg.identities.get(subject)
            if user is None:
                raise PermissionError('Unknown user')
            query = parse_qs(parsed.query, strict_parsing=True)
            expires = int(query['expires'][0])
            signature = query['signature'][0]
            if expires < time.time() or expires > time.time() + 310:
                raise PermissionError('Expired/invalid expiry')
            expected = hmac.new(cfg.signing_secret,
                f'{subject}\n{filename}\n{expires}'.encode('utf-8'),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise PermissionError('Invalid signature')
            dfd = directory_fd(user, 'output', create=False)
            try:
                fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
            finally:
                os.close(dfd)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_DOWNLOAD_BYTES:
                    raise PermissionError('Invalid output file')
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(filename)[0] or 'application/octet-stream')
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(filename, safe=''))
                self.send_header('Content-Length', str(st.st_size))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                if with_body:
                    with os.fdopen(fd, 'rb', closefd=False) as stream:
                        while data := stream.read(64_000):
                            self.wfile.write(data)
            finally:
                os.close(fd)
        except (ValueError, PermissionError, FileNotFoundError, KeyError, IndexError):
            self.send_error(403, 'Invalid or expired download request')
        except OSError:
            self.send_error(404, 'File not found')


class LocalHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Executor must run as root to change Unix identity')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    cfg = Configuration(args.config)
    cfg.socket_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cfg.socket_path.unlink()
    except FileNotFoundError:
        pass
    with ThreadedUnixServer(str(cfg.socket_path), ExecutorHandler) as executor:
        executor.config = cfg
        os.chown(cfg.socket_path, 0, grp.getgrnam(cfg.socket_group).gr_gid)
        os.chmod(cfg.socket_path, 0o660)
        with LocalHTTPServer((cfg.http_host, cfg.http_port), DownloadHandler) as http_server:
            http_server.config = cfg
            thread = threading.Thread(target=http_server.serve_forever, name='download-http', daemon=True)
            thread.start()
            LOG.info('Ready: local unix=%s, download-http=%s:%d; %d configured principals',
                     cfg.socket_path, cfg.http_host, cfg.http_port, len(cfg.identities))
            executor.serve_forever(poll_interval=0.2)


if __name__ == '__main__':
    main()
