"""aishell v2 OAuth 2.1 MCP server for ChatGPT Plus and multiple Unix users.

Unprivileged public MCP endpoint with dynamic registration, PKCE, per-account
Argon2 authentication and per-tool OAuth scopes. Privileged tools are carried
via a private Unix socket to the independent v2 executor; no legacy FastAPI,
Google OAuth, or legacy API token is needed for MCP execution.

This module intentionally preserves tested ChatGPT MCP compatibility (2026-07-28
server/discover, Mixed auth, header normalization and OAuth challenges).
"""
from __future__ import annotations

import hashlib
import asyncio
import socket
import hmac
import html
import json
import os
import logging
import secrets
import sqlite3
from contextlib import contextmanager
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from mcp import types
from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    OAuthAuthorizationServerProvider, RefreshToken, RegistrationError, TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

ORIGIN = os.environ.get('AISHELL_V2_ORIGIN', 'https://aishell.gleixner.xyz').rstrip('/')
RESOURCE = ORIGIN + '/mcp'
SCOPE = 'aishell:ping'
CONTROL_SCOPE = 'aishell:control'
FILES_SCOPE = 'aishell:files'
ALL_SCOPES = [SCOPE, CONTROL_SCOPE, FILES_SCOPE]
BRIDGE_SOCKET = os.environ.get('AISHELL_V2_EXECUTOR_SOCKET', '/run/aishell-v2-stage/control.sock')
DB = Path(os.environ.get('AISHELL_V2_OAUTH_DB', '/var/lib/aishell-v2-stage/oauth.sqlite3'))
AUTH_USERS_PATH = Path(os.environ.get('AISHELL_V2_AUTH_USERS', '/etc/aishell-v2/stage-auth-users.json'))
AUTH_USERS = json.loads(AUTH_USERS_PATH.read_text())['users']
for username, account in AUTH_USERS.items():
    if (not isinstance(username, str) or len(username) > 50 or
            not set(account['allowed_scopes']).issubset(set(ALL_SCOPES)) or
            not isinstance(account.get('subject'), str) or not account.get('password_hash')):
        raise ValueError('Invalid administrator-controlled OAuth identity mapping')
AUTH_SUBJECTS = {account['subject'] for account in AUTH_USERS.values()}
if len(AUTH_SUBJECTS) != len(AUTH_USERS):
    raise ValueError('Duplicate OAuth subjects are forbidden')

logger = logging.getLogger('aishell_v2_mcp')
MAX_CLIENTS = 40
PASSWORD_CHECK = PasswordHasher(time_cost=3, memory_cost=32768, parallelism=1)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


@contextmanager
def db():
    con = sqlite3.connect(DB, timeout=10, isolation_level=None)
    try:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA busy_timeout=10000')
        yield con
    finally:
        con.close()


def init_db():
    with db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tickets (id_hash TEXT PRIMARY KEY, data TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS codes (id_hash TEXT PRIMARY KEY, data TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS tokens (id_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS failures (ts REAL NOT NULL);
        ''')


def clean(con: sqlite3.Connection):
    now = time.time()
    con.execute('DELETE FROM tickets WHERE expires < ?', (now,))
    con.execute('DELETE FROM codes WHERE expires < ?', (now,))
    con.execute('DELETE FROM tokens WHERE expires < ?', (now,))
    con.execute('DELETE FROM failures WHERE ts < ?', (now - 900,))


def allowed_callback(url: str) -> bool:
    """Allow only the documented ChatGPT OAuth redirect destinations.

    ChatGPT uses a stable callback with RFC 9207 issuer identification (which
    we advertise and implement), or a unique connector callback for older
    clients. Do not accept arbitrary paths, domains, or redirect parameters.
    Source: https://developers.openai.com/plugins/build/auth#redirect-url
    """
    try:
        u = urlsplit(url)
        if not (u.scheme == 'https' and u.hostname == 'chatgpt.com'
                and u.port is None and u.username is None and u.password is None
                and not u.fragment and not u.query):
            return False
        if u.path == '/connector_platform_oauth_redirect':
            return True
        # Compatibility with callback-ID-specific URLs for existing clients.
        prefix = '/connector/oauth/'
        suffix = u.path[len(prefix):] if u.path.startswith(prefix) else ''
        return bool(suffix and len(suffix) <= 256
                    and all(c.isascii() and (c.isalnum() or c in '-_') for c in suffix))
    except (ValueError, TypeError):
        return False


class AishellOAuth(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with db() as con:
            row = con.execute('SELECT data FROM clients WHERE id=?', (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row['data']) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Restrict OAuth clients to documented ChatGPT callback URIs, including
        # the stable callback used when RFC 9207 issuer identification works.
        urls = client_info.redirect_uris or []
        if not urls or not all(allowed_callback(str(u)) for u in urls):
            raise RegistrationError('invalid_redirect_uri', 'Only ChatGPT OAuth callback URLs are accepted')
        if client_info.token_endpoint_auth_method not in ('client_secret_basic', 'client_secret_post', 'none'):
            raise RegistrationError('invalid_client_metadata', 'Unsupported client authentication method')
        if client_info.client_id is None:
            raise RegistrationError('invalid_client_metadata', 'Client ID required')
        with db() as con:
            clean(con)
            if con.execute('SELECT COUNT(*) FROM clients').fetchone()[0] >= MAX_CLIENTS:
                raise RegistrationError('invalid_client_metadata', 'Client registration limit reached')
            con.execute('INSERT INTO clients (id, data) VALUES (?,?)',
                        (client_info.client_id, client_info.model_dump_json()))

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource not in (None, RESOURCE):
            raise AuthorizeError('invalid_request', 'Unsupported resource')
        scopes = params.scopes or [SCOPE]
        if not set(scopes).issubset(set(ALL_SCOPES)):
            raise AuthorizeError('invalid_scope', 'Unsupported aishell scope')
        ticket = secrets.token_urlsafe(32)
        record = dict(
            client_id=client.client_id, client_name=client.client_name or 'ChatGPT',
            redirect_uri=str(params.redirect_uri), state=params.state,
            scopes=scopes, code_challenge=params.code_challenge,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=RESOURCE, csrf=secrets.token_urlsafe(32),
        )
        with db() as con:
            clean(con)
            con.execute('INSERT INTO tickets VALUES (?,?,?)',
                        (token_hash(ticket), json.dumps(record), time.time() + 300))
        return ORIGIN + '/oauth/consent?' + urlencode({'ticket': ticket})

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        with db() as con:
            row = con.execute('SELECT data,expires FROM codes WHERE id_hash=?',
                              (token_hash(authorization_code),)).fetchone()
        if not row or row['expires'] < time.time():
            return None
        data = json.loads(row['data'])
        if data['client_id'] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, **data)

    def _create_token(self, con: sqlite3.Connection, kind: str, client_id: str,
                      scopes: list[str], resource: str, expires_at: int, subject: str) -> str:
        if subject not in AUTH_SUBJECTS:
            raise TokenError('invalid_grant', 'Unknown OAuth identity')
        token = secrets.token_urlsafe(48)
        data = json.dumps({'client_id': client_id, 'scopes': scopes, 'resource': resource, 'subject': subject})
        con.execute('INSERT INTO tokens VALUES (?,?,?,?)',
                    (token_hash(token), kind, data, expires_at))
        return token

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        with db() as con:
            con.execute('BEGIN IMMEDIATE')
            removed = con.execute('DELETE FROM codes WHERE id_hash=?',
                                  (token_hash(authorization_code.code),)).rowcount
            if removed != 1 or authorization_code.expires_at < time.time():
                con.rollback()
                raise TokenError('invalid_grant', 'Code already redeemed or expired')
            now = int(time.time())
            subject = authorization_code.subject
            access = self._create_token(con, 'access', client.client_id, authorization_code.scopes,
                                        RESOURCE, now + 900, subject)
            refresh = self._create_token(con, 'refresh', client.client_id, authorization_code.scopes,
                                         RESOURCE, now + 30 * 86400, subject)
            con.commit()
        return OAuthToken(access_token=access, token_type='Bearer', expires_in=900,
                          refresh_token=refresh, scope=' '.join(authorization_code.scopes))

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        with db() as con:
            row = con.execute('SELECT data,expires FROM tokens WHERE id_hash=? AND kind=?',
                              (token_hash(refresh_token), 'refresh')).fetchone()
        if not row or row['expires'] < time.time():
            return None
        data = json.loads(row['data'])
        if data['client_id'] != client.client_id:
            return None
        if data['subject'] not in AUTH_SUBJECTS:
            return None
        return RefreshToken(token=refresh_token, expires_at=int(row['expires']), **data)

    async def exchange_refresh_token(self, client: OAuthClientInformationFull,
                                     refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        if not set(scopes).issubset(set(refresh_token.scopes)):
            raise TokenError('invalid_scope', 'Scope escalation denied')
        with db() as con:
            con.execute('BEGIN IMMEDIATE')
            removed = con.execute('DELETE FROM tokens WHERE id_hash=? AND kind=?',
                                  (token_hash(refresh_token.token), 'refresh')).rowcount
            if removed != 1 or (refresh_token.expires_at and refresh_token.expires_at < time.time()):
                con.rollback()
                raise TokenError('invalid_grant', 'Refresh token already used or expired')
            now = int(time.time())
            subject = refresh_token.subject
            allowed = next((set(u['allowed_scopes']) for u in AUTH_USERS.values()
                            if u['subject'] == subject), set())
            if not set(scopes).issubset(allowed):
                con.rollback()
                raise TokenError('invalid_scope', 'Account permissions have changed')
            access = self._create_token(con, 'access', client.client_id, scopes, RESOURCE, now + 900, subject)
            refresh = self._create_token(con, 'refresh', client.client_id, scopes, RESOURCE, now + 30 * 86400, subject)
            con.commit()
        return OAuthToken(access_token=access, token_type='Bearer', expires_in=900,
                          refresh_token=refresh, scope=' '.join(scopes))

    async def load_access_token(self, token: str) -> AccessToken | None:
        with db() as con:
            row = con.execute('SELECT data, expires FROM tokens WHERE id_hash=? AND kind=?',
                              (token_hash(token), 'access')).fetchone()
        if not row or row['expires'] < time.time():
            return None
        data = json.loads(row['data'])
        if data['resource'] != RESOURCE or data['subject'] not in AUTH_SUBJECTS:
            return None
        allowed = next((set(u['allowed_scopes']) for u in AUTH_USERS.values()
                        if u['subject'] == data['subject']), set())
        if not set(data['scopes']).issubset(allowed):
            return None
        return AccessToken(token=token, expires_at=int(row['expires']), claims={'iss': ORIGIN}, **data)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with db() as con:
            con.execute('DELETE FROM tokens WHERE id_hash=?', (token_hash(token.token),))


provider = AishellOAuth()
SCHEMES = [{'type': 'oauth2', 'scopes': [SCOPE]}]
DESCRIPTION = 'Read-only ping on the authenticated aishell server. Returns current UTC timestamp.'

# Every privileged tool has a scope distinct from the original ping-only token.
# The first owner's OAuth grant may NOT authorize control or file transfer.
TOOL_SPECS = [
    dict(name='ping_aishell', title='Ping aishell', scope=SCOPE,
         description=DESCRIPTION, schema={'type': 'object', 'properties': {}, 'additionalProperties': False},
         read_only=True, destructive=False, open_world=False),
    dict(name='runCommand', title='Execute command as authorized Unix user', scope=CONTROL_SCOPE,
         description=('Execute the supplied shell command on the authenticated user-owned aishell server '
                      'as the mapped Unix account (root only for explicitly approved admins), through the v2 executor. '
                      'can modify or delete arbitrary data or execute network operations. '
                      'Ask before irreversible or high-impact actions. Returns stdout, stderr and exit_code. '
                      'For long-running work use tmux. Output is size-limited.'),
         schema={'type': 'object', 'properties': {
             'command': {'type': 'string', 'minLength': 1, 'maxLength': 12000,
                         'description': 'Bash command to run as the authenticated Unix account.'}},
             'required': ['command'], 'additionalProperties': False},
         read_only=False, destructive=True, open_world=True),
    dict(name='read_tmux_buffer', title='Read tmux terminal', scope=CONTROL_SCOPE,
         description=('Capture the current pane text of a persistent tmux session on the aishell server, '
                      'creating the session if absent. Read the buffer before sending keys.'),
         schema={'type': 'object', 'properties': {
             'session': {'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9_-]{0,48}$',
                         'description': 'tmux session name, typically default'}},
             'required': ['session'], 'additionalProperties': False},
         read_only=False, destructive=False, open_world=False),
    dict(name='send_tmux_keys', title='Send terminal keys to tmux', scope=CONTROL_SCOPE,
         description=('Send a list of literal tmux send-keys arguments to the user-owned persistent tmux '
                      'session, then return the captured pane. Use ["command", "Enter"] to run text, '
                      'or tmux keys such as "C-c". First inspect session with read_tmux_buffer. '
                      'Commands run as the mapped Unix account, with root privileges only for admins.'),
         schema={'type': 'object', 'properties': {
             'session': {'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9_-]{0,48}$'},
             'input': {'type': 'array', 'minItems': 1, 'maxItems': 48,
                       'items': {'type': 'string', 'minLength': 1, 'maxLength': 1024}}},
             'required': ['session', 'input'], 'additionalProperties': False},
         read_only=False, destructive=True, open_world=True),
    dict(name='transferFile', title='Transfer OpenAI files to aishell', scope=FILES_SCOPE,
         description=('Transfer up to 6 user-provided files by downloading their HTTPS OpenAI '
                      'download_link URLs and saving into ~/input on the aishell server. '
                      'Requires file reference objects containing name and download_link. '
                      'Only approved OpenAI file-service hosts are accepted; 16 MiB max per file. '
                      'If ChatGPT cannot supply a download_link, this tool cannot fetch a file ID alone.'),
         schema={'type': 'object', 'properties': {
             'openaiFileIdRefs': {'type': 'array', 'minItems': 1, 'maxItems': 6,
                                 'items': {'type': 'object', 'properties': {
                                     'name': {'type': 'string'}, 'id': {'type': 'string'},
                                     'mime_type': {'type': 'string'}, 'download_link': {'type': 'string'}},
                                     'required': ['name', 'download_link']}}},
             'required': ['openaiFileIdRefs'], 'additionalProperties': False},
         read_only=False, destructive=True, open_world=True),
    dict(name='transferTextFile', title='Write a text file to aishell', scope=FILES_SCOPE,
         description=('Save up to 24 KB UTF-8 text from the conversation as a named file '
                      'in ~/input on the aishell server. Can overwrite an existing file with '
                      'the same name. Use for source code, configuration snippets or small data files.'),
         schema={'type': 'object', 'properties': {
             'filename': {'type': 'string', 'minLength': 1},
             'content': {'type': 'string', 'maxLength': 24000}},
             'required': ['filename', 'content'], 'additionalProperties': False},
         read_only=False, destructive=True, open_world=False),
    dict(name='getFile', title='Get aishell output file', scope=FILES_SCOPE,
         description=('Return a short-lived signed HTTPS download URL for one file located in '
                      '~/output on the aishell server. The filename must be a basename '
                      'without directories. Download links expire in 5 minutes.'),
         schema={'type': 'object', 'properties': {
             'filename': {'type': 'string', 'minLength': 1}},
             'required': ['filename'], 'additionalProperties': False},
         read_only=True, destructive=False, open_world=False),
]
TOOL_BY_NAME = {spec['name']: spec for spec in TOOL_SPECS}


def describe_tools() -> list[types.Tool]:
    return [types.Tool(
        name=t['name'], title=t['title'], description=t['description'],
        inputSchema=t['schema'],
        annotations=types.ToolAnnotations(
            readOnlyHint=t['read_only'], destructiveHint=t['destructive'],
            idempotentHint=False, openWorldHint=t['open_world'],
        ),
        securitySchemes=[{'type': 'oauth2', 'scopes': [t['scope']]}],
        _meta={'securitySchemes': [{'type': 'oauth2', 'scopes': [t['scope']]}]},
    ) for t in TOOL_SPECS]


def invoke_local_bridge(op: str, arguments: dict, access_token: str) -> dict:
    # The MCP server owns no root credentials. The root-owned v2 executor trusts
    # only this service's Linux UID, checked via SO_PEERCRED. Tool access is
    # independently authorized by OAuth on both sides of the socket.
    payload = json.dumps({'op': op, 'arguments': arguments, 'access_token': access_token}, ensure_ascii=False).encode('utf-8') + b'\n'
    if len(payload) > 60_000:
        raise ValueError('Too much tool input (max 60 KB)')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(85)
        sock.connect(BRIDGE_SOCKET)
        sock.sendall(payload)
        reply = bytearray()
        while not reply.endswith(b'\n'):
            chunk = sock.recv(64_000)
            if not chunk:
                break
            reply.extend(chunk)
            if len(reply) > 150_000:
                raise ValueError('Bridge response exceeds limit')
    if not reply.endswith(b'\n'):
        raise RuntimeError('Incomplete privileged bridge response')
    value = json.loads(reply)
    if not value.get('ok'):
        raise RuntimeError(str(value.get('error', 'Privileged operation failed'))[:500])
    return value['result']

mcp = FastMCP(
    'aishell v2 MCP',
    instructions=('Private aishell on the user-owned server. The root command, tmux '
                  'and file-transfer tools require explicit OAuth scopes and owner approval. '
                  'For long commands use a persistent tmux session. Avoid unnecessary destructive operations.'),
    auth_server_provider=provider,
    auth=AuthSettings(
        issuer_url=ORIGIN,
        resource_server_url=RESOURCE,
        required_scopes=[],  # per-tool scope checks in PublicToolDiscovery
        validate_token_resource=True,
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=ALL_SCOPES, default_scopes=ALL_SCOPES,
        ),
        revocation_options=RevocationOptions(enabled=True),
    ),
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[urlsplit(ORIGIN).hostname, '127.0.0.1:*', 'localhost:*'],
        allowed_origins=['https://chatgpt.com', 'https://chat.openai.com'],
    ),
)


@mcp.tool(
    description=DESCRIPTION,
    annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                      idempotentHint=True, openWorldHint=False),
    meta={'securitySchemes': SCHEMES},
)
def ping_aishell() -> str:
    """Return a diagnostic marker and live UTC time after OAuth authentication."""
    return ('AISHELL_MCP_PLUS_TEST_OK (OAuth authorized; ping read-only; UTC='
            + datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') + ')')


@mcp._mcp_server.list_tools()
async def list_tools() -> list[types.Tool]:
    return describe_tools()


def frame(body: str) -> str:
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>aishell authorization</title><style>'
            'body{font:16px system-ui, sans-serif;background:#f8fafc;color:#17212b;'
            'max-width:560px;margin:8vh auto;padding:24px}'
            'main{background:white;border:1px solid #ddd;border-radius:18px;padding:32px}'
            'input{font:inherit;padding:10px;width:100%;box-sizing:border-box}'
            'button{font:inherit;padding:10px 20px;margin-top:14px;cursor:pointer}'
            'small{overflow-wrap:anywhere;color:#526070}code{overflow-wrap:anywhere}'
            '</style></head><body><main>' + body + '</main></body></html>')


def secure_headers(response):
    response.headers.update({
        'Cache-Control': 'no-store', 'Pragma': 'no-cache',
        'X-Frame-Options': 'DENY', 'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
        'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    })
    return response


def oauth_redirect(record: dict, **params: str):
    fields = dict(params)
    if record.get('state') is not None:
        fields['state'] = record['state']
    fields['iss'] = ORIGIN
    return secure_headers(RedirectResponse(construct_redirect_uri(record['redirect_uri'], **fields), status_code=303))


@mcp.custom_route('/oauth/consent', methods=['GET', 'POST'])
async def consent(request: Request):
    if request.method == 'GET':
        ticket = request.query_params.get('ticket', '')
        if not (32 <= len(ticket) <= 100):
            return secure_headers(PlainTextResponse('Invalid or expired authorization request', status_code=400))
        with db() as con:
            row = con.execute('SELECT data, expires FROM tickets WHERE id_hash=?',
                              (token_hash(ticket),)).fetchone()
        if not row or row['expires'] < time.time():
            return secure_headers(PlainTextResponse('Authorization request expired', status_code=400))
        record = json.loads(row['data'])
        c = html.escape
        granted = set(record['scopes'])
        privileged = CONTROL_SCOPE in granted
        file_access = FILES_SCOPE in granted
        permission_text = ', '.join(c(item) for item in record['scopes'])
        warnings = (
            '<p style="color:#9b1c1c"><strong>HIGH PRIVILEGE:</strong> This grants ChatGPT '
            'shell execution and persistent tmux access as the account assigned '
            'by the administrator. Owner accounts may have UNRESTRICTED ROOT access. '
            'Commands can modify, delete, or transmit accessible data.</p>'
            if privileged else '<p>No shell execution is included in this request.</p>'
        )
        if file_access:
            warnings += ('<p>File permissions permit transferring files into ~/input and '
                         'generating links to files in ~/output.</p>')
        page = (f'<h1>Authorize aishell</h1>'
                f'<p>Allow <strong>{c(record["client_name"])}</strong> to use these aishell tools?</p>'
                f'<p><strong>Requested scopes:</strong> {permission_text}.</p>'
                f'<p><small>Callback: {c(record["redirect_uri"])}</small></p>'
                + warnings +
                '<form method="post" action="/oauth/consent">'
                f'<input type="hidden" name="ticket" value="{c(ticket)}">'
                f'<input type="hidden" name="csrf" value="{c(record["csrf"])}">'
                '<label for="username">aishell account</label>'
                '<input id="username" type="text" name="username" value="owner" required autocomplete="username">'
                '<label for="pw">aishell account password</label>'
                '<input id="pw" type="password" name="password" required autocomplete="current-password">'
                '<button type="submit" name="decision" value="approve">Authorize ChatGPT</button> '
                '<button type="submit" name="decision" value="deny" formnovalidate>Cancel</button>'
                '</form>')
        resp = secure_headers(HTMLResponse(frame(page)))
        # Firefox follows Referrer-Policy: no-referrer when submitting a regular
        # same-origin HTML form by sending Origin: null. This broke our strict
        # POST Origin check. The 'origin' policy sends only our scheme/host,
        # never the consent URL's secret ticket query parameter, and produces
        # Origin: https://aishell.gleixner.xyz on Firefox form submissions.
        # Keep no-referrer on OAuth redirects / token responses.
        resp.headers['Referrer-Policy'] = 'origin'
        resp.set_cookie('aishell_oauth_csrf', record['csrf'], secure=True, httponly=True,
                        samesite='lax', max_age=300, path='/oauth/consent')
        return resp

    if request.headers.get('origin') != ORIGIN:
        return secure_headers(PlainTextResponse('Invalid form origin', status_code=403))
    form = await request.form()
    ticket = str(form.get('ticket', ''))
    csrf = str(form.get('csrf', ''))
    cookie = request.cookies.get('aishell_oauth_csrf', '')
    if not (32 <= len(ticket) <= 100):
        return secure_headers(PlainTextResponse('Invalid authorization request', status_code=400))

    with db() as con:
        row = con.execute('SELECT data,expires FROM tickets WHERE id_hash=?',
                          (token_hash(ticket),)).fetchone()
    if not row or row['expires'] < time.time():
        return secure_headers(PlainTextResponse('Authorization request expired', status_code=400))
    record = json.loads(row['data'])
    if (not csrf or not cookie or not hmac.compare_digest(csrf, cookie)
            or not hmac.compare_digest(csrf, record['csrf'])):
        return secure_headers(PlainTextResponse('CSRF check failed', status_code=403))

    if form.get('decision') == 'deny':
        with db() as con:
            con.execute('DELETE FROM tickets WHERE id_hash=?', (token_hash(ticket),))
        return oauth_redirect(record, error='access_denied')
    if form.get('decision') != 'approve':
        return secure_headers(PlainTextResponse('Unknown approval decision', status_code=400))

    password = str(form.get('password', ''))
    username = str(form.get('username', ''))
    if len(password) > 256 or len(username) > 50:
        return secure_headers(PlainTextResponse('Invalid credentials', status_code=403))
    with db() as con:
        clean(con)
        recent_failures = con.execute('SELECT COUNT(*) FROM failures').fetchone()[0]
    if recent_failures >= 8:
        return secure_headers(PlainTextResponse('Too many attempts. Try later.', status_code=429))
    account = AUTH_USERS.get(username)
    authorized = False
    if account and set(record['scopes']).issubset(set(account['allowed_scopes'])):
        try:
            PASSWORD_CHECK.verify(account['password_hash'], password)
            authorized = True
        except (VerifyMismatchError, VerificationError):
            pass
    if not authorized:
        with db() as con:
            con.execute('INSERT INTO failures(ts) VALUES (?)', (time.time(),))
        return secure_headers(PlainTextResponse('Invalid credentials or permissions', status_code=403))

    code = secrets.token_urlsafe(40)
    expires = time.time() + 180
    auth_code = AuthorizationCode(
        code=code, client_id=record['client_id'], scopes=record['scopes'],
        code_challenge=record['code_challenge'], expires_at=expires,
        redirect_uri=record['redirect_uri'],
        redirect_uri_provided_explicitly=record['redirect_uri_provided_explicitly'],
        resource=RESOURCE, subject=account['subject'],
    )
    code_data = auth_code.model_dump(mode='json', exclude={'code'})
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        removed = con.execute('DELETE FROM tickets WHERE id_hash=?',
                              (token_hash(ticket),)).rowcount
        if removed != 1 or row['expires'] < time.time():
            con.rollback()
            return secure_headers(PlainTextResponse('Authorization request used or expired', status_code=400))
        con.execute('INSERT INTO codes VALUES (?,?,?)',
                    (token_hash(code), json.dumps(code_data), expires))
        con.execute('DELETE FROM failures')
        con.commit()
    resp = oauth_redirect(record, code=code)
    resp.delete_cookie('aishell_oauth_csrf', path='/oauth/consent')
    return resp


# Override the SDK's basic RFC8414 metadata to advertise RFC9207 `iss` support,
# which we implement on both success and denial redirects above.
METADATA = {
    'issuer': ORIGIN,
    'authorization_endpoint': ORIGIN + '/authorize',
    'token_endpoint': ORIGIN + '/token',
    'registration_endpoint': ORIGIN + '/register',
    'revocation_endpoint': ORIGIN + '/revoke',
    'response_types_supported': ['code'],
    'grant_types_supported': ['authorization_code', 'refresh_token'],
    'code_challenge_methods_supported': ['S256'],
    'token_endpoint_auth_methods_supported': ['none', 'client_secret_basic', 'client_secret_post'],
    'revocation_endpoint_auth_methods_supported': ['none', 'client_secret_basic', 'client_secret_post'],
    'scopes_supported': ALL_SCOPES,
    'authorization_response_iss_parameter_supported': True,
    'client_id_metadata_document_supported': False,
}


def oauth_metadata(request: Request):
    return secure_headers(JSONResponse(METADATA))


def resource_metadata(request: Request):
    # SDK's Pydantic AnyHttpUrl normalizes a bare issuer host to a trailing
    # slash. Use the precise canonical issuer without slash here to match the
    # RFC8414 metadata's issuer and callback `iss` byte-for-byte.
    return secure_headers(JSONResponse({
        'resource': RESOURCE,
        'authorization_servers': [ORIGIN],
        'scopes_supported': ALL_SCOPES,
        'bearer_methods_supported': ['header'],
    }))


class EnsureIssuerOnOAuthErrors:
    """Ensure RFC 9207 issuer on authorization error redirects from the MCP SDK.

    The SDK's AuthorizationHandler does not automatically add `iss` on some
    error redirects; the authorization server advertises RFC 9207 support.
    Only modify validated ChatGPT callback URLs with code/error parameters.
    """
    def __init__(self, downstream):
        self.downstream = downstream

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') != '/authorize':
            return await self.downstream(scope, receive, send)

        async def ensure_iss(msg):
            if msg['type'] == 'http.response.start' and 300 <= msg['status'] < 400:
                headers = list(msg.get('headers', []))
                for index, (key, raw) in enumerate(headers):
                    if key.lower() != b'location':
                        continue
                    url = raw.decode('latin-1')
                    parsed = urlsplit(url)
                    target_no_query = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
                    q = parse_qsl(parsed.query, keep_blank_values=True)
                    names = [name for name, _ in q]
                    if (allowed_callback(target_no_query)
                            and ('code' in names or 'error' in names)
                            and 'iss' not in names):
                        q.append(('iss', ORIGIN))
                        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                                        urlencode(q), parsed.fragment))
                        headers[index] = (key, url.encode('latin-1'))
                        msg['headers'] = headers
                    break
            await send(msg)
        return await self.downstream(scope, receive, ensure_iss)


class PublicToolDiscovery:
    """Mixed-auth compatibility: only initialization and tool *discovery* are public.

    The underlying MCP SDK's OAuth middleware protects every MCP request,
    including harmless methods that ChatGPT must call to create a mixed-auth
    plugin. This narrow ASGI shim answers exactly those fixed public methods.
    Every tool invocation stays behind SDK bearer-token validation.
    """
    def __init__(self, downstream):
        self.downstream = downstream
        self.public_tools = [tool.model_dump(mode='json', by_alias=True, exclude_none=True)
                             for tool in describe_tools()]
        self.bridge_slots = asyncio.Semaphore(4)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') != '/mcp' or scope.get('method') != 'POST':
            return await self.downstream(scope, receive, send)
        headers = dict((k.lower(), v) for k, v in scope.get('headers', []))
        content_type = headers.get(b'content-type', b'').split(b';', 1)[0].strip().decode('latin1', 'replace')
        accept = headers.get(b'accept', b'').decode('latin1', 'replace')[:120]
        has_auth = b'authorization' in headers
        ua = headers.get(b'user-agent', b'').decode('latin1', 'replace')[:80]
        # Log only request METHOD and transport metadata, never JSON-RPC parameters,
        # credentials, access tokens, IP addresses, or personal file contents.
        if content_type != 'application/json':
            logger.info('MCP probe passthrough reason=nonjson content_type=%s accept=%s has_auth=%s ua=%s',
                        content_type[:64], accept, has_auth, ua)
            return await self.downstream(scope, receive, send)
        request = Request(scope, receive)
        try:
            raw = await request.body()
            if len(raw) > 65536:
                return await JSONResponse({'error': 'Request too large'}, status_code=413)(scope, receive, send)
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return await JSONResponse({'error': 'Invalid JSON'}, status_code=400)(scope, receive, send)
        if not isinstance(payload, dict):
            return await JSONResponse({'error': 'Expected JSON-RPC object'}, status_code=400)(scope, receive, send)
        method = payload.get('method')
        safe_method = method if method in ('initialize', 'server/discover', 'tools/list', 'notifications/initialized', 'tools/call') else 'other'
        # Unknown RPC methods are logged as a bounded safe token, without
        # request parameters or arbitrary attacker-controlled log text.
        unknown_name = ''
        if safe_method == 'other':
            if isinstance(method, str):
                import re
                unknown_name = re.sub(r'[^a-zA-Z0-9_./-]', '_', method)[:75]
            else:
                unknown_name = 'non-string-' + type(method).__name__
        logger.info('MCP probe request method=%s unknown_name=%s keys=%s has_auth=%s content_type=%s accept=%s ua=%s',
                    safe_method, unknown_name, ','.join(sorted(str(k) for k in payload.keys()))[:120],
                    has_auth, content_type[:64], accept, ua)
        # MCP 2026-07-28 uses mandatory per-request protocol metadata and
        # `server/discover` instead of the legacy initialization handshake.
        # Explicit modern support is kept separate from the SDK's 2025 transport.
        params = payload.get('params', {})
        meta = params.get('_meta', {}) if isinstance(params, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        version = headers.get(b'mcp-protocol-version', b'').decode('latin1', 'replace')
        version_from_meta = meta.get('io.modelcontextprotocol/protocolVersion')
        modern = (version == '2026-07-28' or version_from_meta == '2026-07-28'
                  or method == 'server/discover')
        if modern and version and version != '2026-07-28':
            return await JSONResponse({
                'jsonrpc': '2.0', 'id': payload.get('id'),
                'error': {'code': -32022, 'message': 'Unsupported protocol version',
                          'data': {'requested': version, 'supported': ['2026-07-28']}},
            }, status_code=400)(scope, receive, send)

        # Public discovery is allowed, including for modern protocol callers.
        # No privileged operations or private content are exposed.
        if method not in ('initialize', 'server/discover', 'tools/list', 'notifications/initialized', 'ping'):
            if method == 'tools/call' and isinstance(payload.get('id'), (str, int)):
                tool_name = params.get('name') if isinstance(params, dict) else None
                spec = TOOL_BY_NAME.get(tool_name) if isinstance(tool_name, str) else None
                if spec is None:
                    result = {'isError': True, 'content': [{'type': 'text', 'text': 'Unknown MCP tool'}]}
                    if modern:
                        result['resultType'] = 'complete'
                    return await JSONResponse({'jsonrpc': '2.0', 'id': payload['id'], 'result': result},
                                              headers={'Cache-Control': 'no-store'})(scope, receive, send)

                def require_oauth():
                    challenge = ('Bearer resource_metadata="' + ORIGIN
                                 + '/.well-known/oauth-protected-resource/mcp",'
                                 + ' error="insufficient_scope",'
                                 + ' error_description="Authorize aishell scope ' + spec['scope'] + '",'
                                 + ' scope="' + spec['scope'] + '"')
                    result = {
                        'content': [{'type': 'text', 'text': 'OAuth authorization required for ' + spec['scope']}],
                        '_meta': {'mcp/www_authenticate': [challenge]},
                        'isError': True,
                    }
                    if modern:
                        result['resultType'] = 'complete'
                    return JSONResponse({'jsonrpc': '2.0', 'id': payload['id'], 'result': result},
                                        headers={'Cache-Control': 'no-store'})

                if not has_auth:
                    return await require_oauth()(scope, receive, send)
                auth = headers[b'authorization'].decode('ascii', 'ignore')
                if not auth.startswith('Bearer ') or len(auth) > 512:
                    return await JSONResponse({'error': 'invalid_token'}, status_code=401)(scope, receive, send)
                token = await provider.load_access_token(auth[7:])
                if not token or token.resource != RESOURCE or token.claims.get('iss') != ORIGIN:
                    return await JSONResponse({'error': 'invalid_token'}, status_code=401)(scope, receive, send)
                if spec['scope'] not in token.scopes:
                    # Existing ping-only grants cannot run privileged tools. A
                    # fresh interactive OAuth approval is required for scope upgrades.
                    return await require_oauth()(scope, receive, send)
                arguments = params.get('arguments', {}) if isinstance(params, dict) else {}
                if not isinstance(arguments, dict):
                    result = {'isError': True, 'content': [{'type': 'text', 'text': 'Tool arguments must be a JSON object'}]}
                else:
                    try:
                        if tool_name == 'ping_aishell':
                            if arguments:
                                raise ValueError('Ping does not accept arguments')
                            data = {'message': ping_aishell()}
                        else:
                            async with self.bridge_slots:
                                data = await asyncio.to_thread(invoke_local_bridge, tool_name, arguments, auth[7:])
                        result = {
                            'isError': False,
                            'structuredContent': data,
                            'content': [{'type': 'text', 'text':
                                         data['message'] if tool_name == 'ping_aishell'
                                         else json.dumps(data, ensure_ascii=False)}],
                        }
                    except Exception as exc:
                        logger.warning('Authorized tool %s failed: %s', tool_name, type(exc).__name__)
                        result = {'isError': True, 'content': [{'type': 'text', 'text':
                                             (type(exc).__name__ + ': ' + str(exc)[:450])}]}
                if modern:
                    result['resultType'] = 'complete'
                return await JSONResponse({'jsonrpc': '2.0', 'id': payload['id'], 'result': result},
                                          headers={'Cache-Control': 'no-store'})(scope, receive, send)
            # Unknown methods keep the original SDK's secured transport behavior.
            return await JSONResponse({'error': 'invalid_token'}, status_code=401)(scope, receive, send)
        if method == 'notifications/initialized':
            return await PlainTextResponse('', status_code=202)(scope, receive, send)
        if not isinstance(payload.get('id'), (str, int)):
            return await JSONResponse({'error': 'Missing JSON-RPC request id'}, status_code=400)(scope, receive, send)
        if method == 'initialize':
            result = {
                'protocolVersion': '2025-06-18',
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': 'aishell v2 MCP', 'version': '1.0.0'},
                'instructions': 'Private user-owned aishell. Root shell, tmux and file tools require explicit OAuth permissions and owner approval.',
            }
        elif method == 'server/discover':
            result = {
                'resultType': 'complete',
                'supportedVersions': ['2026-07-28'],
                'capabilities': {'tools': {'listChanged': False}},
                '_meta': {'io.modelcontextprotocol/serverInfo': {
                    'name': 'aishell v2 MCP', 'version': '1.0.0'}},
                'instructions': 'Private user-owned aishell. Root shell, tmux and file tools require explicit OAuth permissions and owner approval.',
                'ttlMs': 60000,
                'cacheScope': 'public',
            }
        elif method == 'ping':
            result = {}
        else:
            result = {'tools': self.public_tools}
            if modern:
                result['resultType'] = 'complete'
        return await JSONResponse({
            'jsonrpc': '2.0', 'id': payload['id'], 'result': result,
        }, headers={'Cache-Control': 'no-store'})(scope, receive, send)


class MCPHeaderNormalization:
    """Repair the OpenAI Mixed-auth tool scanner's nonstandard MCP headers.

    ChatGPT's connector scanner has been observed to use
    Content-Type: application/octet-stream and Accept: */* for JSON-RPC
    payloads. RFC 2025-06-18 Streamable HTTP uses application/json, so normalize
    those *headers only*. This is applied strictly to POST /mcp; other routes
    and all authorization data are unchanged. Invalid or binary bodies do not
    gain access to any protected operation; PublicToolDiscovery rejects them.
    See https://github.com/openai/openai-apps-sdk-examples/issues/183
    """
    def __init__(self, downstream):
        self.downstream = downstream

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') != '/mcp' or scope.get('method') != 'POST':
            return await self.downstream(scope, receive, send)
        normalized = []
        seen_accept = False
        seen_ct = False
        for key, value in scope.get('headers', []):
            lk = key.lower()
            if lk == b'content-type':
                seen_ct = True
                if value.lower().split(b';', 1)[0].strip() in (
                        b'application/octet-stream', b'text/octet-stream'):
                    logger.info('OpenAI compatibility: normalized octet-stream content type')
                    normalized.append((key, b'application/json'))
                    continue
            elif lk == b'accept':
                seen_accept = True
                if value.lower().strip() in (b'*/*', b'*', b'application/*'):
                    logger.info('OpenAI compatibility: normalized wildcard Accept header')
                    normalized.append((key, b'application/json, text/event-stream'))
                    continue
            normalized.append((key, value))
        if not seen_accept:
            normalized.append((b'accept', b'application/json, text/event-stream'))
        if not seen_ct:
            normalized.append((b'content-type', b'application/json'))
        amended = dict(scope)
        amended['headers'] = normalized
        await self.downstream(amended, receive, send)


init_db()
starlette_app = mcp.streamable_http_app()
starlette_app.router.routes[:] = [
    Route('/.well-known/oauth-authorization-server', endpoint=oauth_metadata, methods=['GET']),
    # Compatibility fallback for OpenAI clients probing OIDC metadata after
    # RFC8414. It returns the same OAuth metadata, not an OIDC ID-token issuer.
    Route('/.well-known/openid-configuration', endpoint=oauth_metadata, methods=['GET']),
    Route('/.well-known/oauth-protected-resource/mcp', endpoint=resource_metadata, methods=['GET']),
    Route('/.well-known/oauth-protected-resource', endpoint=resource_metadata, methods=['GET']),
] + [r for r in starlette_app.router.routes if r.path not in (
    '/.well-known/oauth-authorization-server',
    '/.well-known/oauth-protected-resource/mcp',
)]
app = MCPHeaderNormalization(PublicToolDiscovery(EnsureIssuerOnOAuthErrors(starlette_app)))
