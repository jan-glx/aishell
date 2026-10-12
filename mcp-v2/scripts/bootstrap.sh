#!/usr/bin/env bash
# aishell v2 standalone installer, DOES NOT modify nginx or legacy FastAPI.
# Fresh Debian 12 host prerequisites: python3-venv, tmux, systemd, nginx + TLS.
# Installation only: does not automatically enable, start, or migrate services.
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Run as root with sudo' >&2; exit 1; }
: "${AISHELL_DOMAIN:?Set AISHELL_DOMAIN to the public HTTPS hostname}"
[[ "$AISHELL_DOMAIN" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || { echo 'Invalid hostname' >&2; exit 1; }
MCP_USER="${AISHELL_MCP_USER:-aishell-mcp-test}"
export AISHELL_MCP_USER="$MCP_USER"
[[ "$MCP_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo 'Invalid MCP service account' >&2; exit 1; }
OAUTH_DB="${AISHELL_OAUTH_DB:-/var/lib/aishell-v2/oauth.sqlite3}"
[[ "$OAUTH_DB" = /* && "$OAUTH_DB" != *$'\n'* ]] || { echo 'Invalid OAuth DB path' >&2; exit 1; }
BASE="$(cd "$(dirname "$0")/.." && pwd)"
command -v tmux >/dev/null || { echo 'Install tmux first' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Install python3 first' >&2; exit 1; }
python3 -m venv --help >/dev/null || { echo 'Install python3-venv first' >&2; exit 1; }
if ! id "$MCP_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$MCP_USER"
fi
install -d -m 0755 /opt/aishell-v2
install -d -m 0750 -o root -g "$MCP_USER" /etc/aishell-v2
install -d -m 0700 /root/.config/aishell-v2
install -d -m 0700 /var/lib/aishell-v2
python3 -m venv /opt/aishell-v2/.venv
/opt/aishell-v2/.venv/bin/python -m pip install -q -r "$BASE/requirements.lock"
install -m 0644 "$BASE/src/executor.py" /opt/aishell-v2/executor.py
install -m 0644 "$BASE/src/mcp_shadow.py" /opt/aishell-v2/mcp_shadow.py

if [[ ! -e /etc/aishell-v2/downloads.key ]]; then
    /opt/aishell-v2/.venv/bin/python - <<'PY'
from pathlib import Path
import secrets
p=Path('/etc/aishell-v2/downloads.key')
p.write_text(secrets.token_urlsafe(48)+'\n')
p.chmod(0o600)
PY
fi

if [[ ! -e /etc/aishell-v2/config.json ]]; then
    AISHELL_MCP_USER="$MCP_USER" AISHELL_OAUTH_DB="$OAUTH_DB" AISHELL_DOMAIN="$AISHELL_DOMAIN" python3 - <<'PY'
import json,os
from pathlib import Path
host=os.environ['AISHELL_DOMAIN']; user=os.environ['AISHELL_MCP_USER']
record={
 'issuer':'https://'+host,
 'resource':'https://'+host+'/mcp',
 'oauth_database':os.environ['AISHELL_OAUTH_DB'],
 'socket_path':'/run/aishell-v2/control.sock',
 'socket_group':user,
 'permitted_socket_users':[user],
 'download_signing_key':'/etc/aishell-v2/downloads.key',
 'download_listen_host':'127.0.0.1',
 'download_listen_port':8780,
 'public_download_prefix':'https://'+host+'/mcp-v2/download',
 'principals':{'local-aishell-owner':{
    'unix_user':'root','allow_root':True,
    'allowed_scopes':['aishell:control','aishell:files']
 }},
}
p=Path('/etc/aishell-v2/config.json');p.write_text(json.dumps(record,indent=2)+'\n');p.chmod(0o600)
PY
fi

if [[ ! -e /etc/aishell-v2/auth-users.json ]]; then
    # Do not reuse Google OAuth credentials. Install a dedicated strong login.
    if [[ ! -e /root/.config/aishell-v2/owner-password ]]; then
        /opt/aishell-v2/.venv/bin/python - <<'PY'
import secrets
from pathlib import Path
p=Path('/root/.config/aishell-v2/owner-password')
p.write_text(secrets.token_urlsafe(36)+'\n');p.chmod(0o600)
PY
    fi
    /opt/aishell-v2/.venv/bin/python - <<'PY'
import json,os,grp
from pathlib import Path
from argon2 import PasswordHasher
user=os.environ.get('AISHELL_MCP_USER','aishell-mcp-test')
pw=Path('/root/.config/aishell-v2/owner-password').read_text().strip()
record={'users':{'owner':{
  'subject':'local-aishell-owner','display_name':'Owner',
  'password_hash':PasswordHasher(time_cost=3,memory_cost=32768,parallelism=1).hash(pw),
  'allowed_scopes':['aishell:ping','aishell:control','aishell:files']
}}}
p=Path('/etc/aishell-v2/auth-users.json')
p.write_text(json.dumps(record,indent=2)+'\n')
os.chown(p,0,grp.getgrnam(user).gr_gid);p.chmod(0o640)
PY
fi

DBDIR="$(dirname "$OAUTH_DB")"
if [[ "$DBDIR" == '/var/lib/aishell-v2' ]]; then
    chown "$MCP_USER:$MCP_USER" "$DBDIR"
    chmod 0700 "$DBDIR"
fi
# Preserve existing OAuth database ownership if migrating active clients.
if [[ ! -e /etc/systemd/system/aishell-v2.service ]]; then
    install -m 0644 "$BASE/deploy/aishell-v2.service" /etc/systemd/system/aishell-v2.service
fi
if [[ ! -e /etc/systemd/system/aishell-v2-mcp.service ]]; then
    sed -e "s@https://example.invalid@https://${AISHELL_DOMAIN}@g" \
        -e "s@/var/lib/aishell-v2/oauth.sqlite3@${OAUTH_DB}@g" \
        "$BASE/deploy/aishell-v2-mcp.service" > /etc/systemd/system/aishell-v2-mcp.service
    chmod 0644 /etc/systemd/system/aishell-v2-mcp.service
fi
systemctl daemon-reload
printf '\nInstalled aishell v2 files and independent OAuth configuration.\n'
printf 'No services enabled or started; no nginx or legacy FastAPI files changed.\n'
printf 'Admin login password location: /root/.config/aishell-v2/owner-password (root-only).\n'
printf 'Review /etc/aishell-v2/config.json and systemd units before activation.\n'
printf 'Only add nginx MCP-specific routes after separate tests and backup.\n'
