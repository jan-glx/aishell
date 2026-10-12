# aishell v2 — independent MCP + multi-user executor

**Project state: implemented and successfully tested in staging, not yet cut over to the public ChatGPT connector.**

The new implementation has no dependency on the old root FastAPI `aishell.service` or its API token. It is explicitly designed to be installed *alongside* a server that also runs unrelated websites, APIs, mail, and services.

## Architecture

```text
ChatGPT Plus (existing private MCP connector)
       |
       | HTTPS OAuth 2.1 + MCP
       v
nginx (existing multi-app virtual host, shared; KEEP other routes)
       |
       +-- /mcp, /authorize, /token, /register, /revoke ...
       |
       v
MCP + OAuth (unprivileged Linux account)
    /opt/aishell-v2/mcp_shadow.py
    - 2026-07-28 server/discover and legacy 2025 protocol compatibility
    - OpenAI Mixed authentication semantics
    - DCR, PKCE S256, individual accounts, Argon2id passwords
    - OAuth tokens/rotation/revocation, per-tool scopes, owner consent
       |
       | Unix socket: Linux SO_PEERCRED + OAuth bearer
       v
aishell v2 executor (root-owned service)
    /opt/aishell-v2/executor.py
    - independent SQLite OAuth scope/resource/subject validation
    - mandatory server-side subject -> passwd UID/GID mapping
    - subprocess privilege drop per UID, no caller-controlled username
    - per-user tmux -L aishell-v2 sessions
    - per-user ~/input, ~/output with safe directory access
    - pinned-TLS OpenAI file fetch, signed 5-minute output downloads
       |
       +-- root owner OR individual unprivileged Unix accounts
       +-- localhost:8780 signed downloads (nginx proxy at cutover)
```

An OAuth *subject* identifies who has authenticated; scopes identify what that person is allowed to do. The executor alone decides which Unix account represents that subject using root-owned `/etc/aishell-v2/config.json`. All processes for a guest run as that Unix UID, not root. OAuth clients cannot select a UID by tool parameter.

## Services on the current server (2026-10-12)

| Service | Role | Status/cutover |
| --- | --- | --- |
| `aishell.service` | Existing FastAPI with other functionality | **Leave running**; not part of v2 removal |
| `aishell-mcp-test.service` | Currently public authenticated MCP plugin | **Leave running** until explicit v2 cutover |
| `aishell-mcp-bridge.service` | Existing root bridge to old FastAPI | **Leave running** until public v2 cutover |
| `aishell-v2.service` | New standalone direct executor, root process | Standby, socket-only and localhost downloads |
| `aishell-v2-stage.service` | Isolated two-user executor with synthetic OAuth tokens | Testing only; stop after test |
| `aishell-v2-mcp-shadow.service` | Isolated staging MCP/OAuth, loopback TCP 8767 | Testing only; stop after test |

The public `https://aishell.gleixner.xyz/mcp` remains on existing port 8766. **Production nginx configuration, all original FastAPI routes, and Google OAuth were not modified during v2 development.** No other nginx application (`/klangwald`, `/nomdebebe`, homepage, `/static`, `/output`, legacy API) is to be removed as part of MCP migration.

## Code and configuration

| File | Purpose |
| --- | --- |
| `src/executor.py` | Direct root/Unix-user execution supervisor, tools and signed download listener (Python standard library) |
| `src/mcp_shadow.py` | MCP/ChatGPT protocol compatibility and multi-user OAuth server (MCP SDK) |
| `requirements.lock` | Pinned v2 MCP Python environment |
| `tests/test_executor.py` | Unix socket, tokens, UID isolation, tmux, file and download security |
| `tests/test_mcp_shadow.py` | End-to-end DCR → OAuth user login → MCP → direct executor integration |
| `deploy/*.service` | Independent systemd units; **none should replace unrelated site/app units** |
| `deploy/nginx-mcp-routes.conf` | MCP-specific locations in a shared nginx HTTPS server (cutover reference) |
| `deploy/nginx-mcp-v2-download.conf` | New signed-download HTTP route to add at cutover |
| `config/*.example.json` | Safe configuration templates, no real secrets |
| `scripts/bootstrap.sh` | Prepare a fresh host with standalone code + configuration, **does not start services or touch nginx** |
| `scripts/add_user.py` | Safely map an existing non-root Unix user to a new OAuth subject and separate password |

Real host configuration and credentials are **not in this repository**:

- `/etc/aishell-v2/config.json` root:root 0600 — subject/Unix mapping and runtime settings.
- `/etc/aishell-v2/auth-users.json` root:aishell-mcp-test 0640 — real OAuth username, subject, password hash and allowed scopes (created on a fresh install; not enabled in production yet).
- `/etc/aishell-v2/downloads.key` root:root 0600 — download URL HMAC key.
- `/var/lib/aishell-v2/oauth.sqlite3` — standalone OAuth client/token database for a fresh deployment.
- Current production OAuth database: `/var/lib/aishell-mcp-test/oauth.sqlite3` — must be preserved if reusing existing ChatGPT client grants during cutover.
- Staging fixtures: `/var/lib/aishell-v2-stage/oauth.sqlite3` and `/etc/aishell-v2/stage-auth-users.json` — **never copy staging credentials into production**.

## Fresh Debian 12 installation

Prerequisites: an HTTPS virtual host for your domain with working TLS certificates, systemd, `python3-venv`, `tmux`, nginx, a root SSH administration path, and Python 3.11. The installer does NOT set up TLS certificates or unrelated applications.

```bash
sudo apt-get install python3-venv python3-pip tmux
cd /path/to/aishell-v2
sudo AISHELL_DOMAIN=mcp.example.com bash scripts/bootstrap.sh
```

This creates an independent venv, root-owned secrets and config, an MCP service account and systemd files. Nothing starts automatically. It prints the path to the root-only initial owner password. If another OAuth server is already running, set `AISHELL_OAUTH_DB` to the intended database path before running, and review migration implications carefully.

**Review first:** `/etc/aishell-v2/config.json`, `/etc/aishell-v2/auth-users.json`, `/etc/systemd/system/aishell-v2.service`, `/etc/systemd/system/aishell-v2-mcp.service`. The MCP unit template binds port 8766. **Do not start it on this host while the existing MCP service owns that port.** On an empty host, after verifying OAuth/client security and backups, start executor then MCP, integrate *only* the required nginx locations, `nginx -t`, reload nginx, configure private ChatGPT connector with Mixed OAuth and DCR.

### Add a new unprivileged user

```bash
sudo useradd --create-home --shell /bin/bash alice
sudo /opt/aishell-v2/.venv/bin/python scripts/add_user.py alice alice
# Check the proposed mappings before restarting services:
sudo cat /etc/aishell-v2/config.json
# Never paste auth-users.json or the generated password hashes into chat.
```

The command prompts for a separate password, and adds `user:alice` → `alice` to root-owned executor mapping and unprivileged OAuth account mapping. It intentionally refuses to map root or an already-mapped Unix account. It does **not** grant sudo; for strong isolation audit filesystem ACLs, groups, home permissions and any existing sudo policies on the host. Restart the two v2 services deliberately after reviewing configuration changes.

## Runtime permissions and behavior

- `aishell:ping` — harmless diagnostics.
- `aishell:control` — shell and persistent tmux as mapped UID. Owner root mapping requires explicit `allow_root: true` in root-only config.
- `aishell:files` — per-user file upload to `~/input` and signed download of files in `~/output`.
- Root service verifies valid opaque OAuth token from database (resource, subject, expiry, tool scope) **again**, separately from MCP validation.
- Per-user processes get explicit `HOME`, `USER`, `LOGNAME`, `PATH` and no inherited API credentials. The executor uses OS-level UID/GID drop with empty supplementary group list.
- Shell commands: max 12 KB command text, 65-second time limit, ~70 KB output per stream. Use tmux for long jobs.
- tmux uses the dedicated `-L aishell-v2` socket namespace. **It intentionally does not attach to sessions from the legacy GPT**; migration of old tmux sessions needs a separate operational plan.
- OpenAI file fetches allow only documented OpenAI file-host patterns, use validated public DNS with pinned TLS server hostname and no HTTP redirects. Limit 16 MiB/file. **Genuine ChatGPT-uploaded file IDs/URLs still need an end-to-end user-interface test.**
- Signed files: root executor issues HTTPS URLs with 5-minute HMAC expiration, currently hosted at `/mcp-v2/download/...`. The staging test exercises these over localhost:8781. Public file links **will not work** until its dedicated nginx location is added at cutover.
- Public tool selection, `runCommand`, and consent still carry prompt-injection/destructive-command risks; restrict plugin sharing to trusted people and require approvals for high-impact actions.

## Verified in isolated staging

Staging uses a real root-owned executor and unprivileged MCP HTTP server, **not mocks of shell calls**. Tests demonstrated:

- OAuth DCR, authorization code PKCE, separate `owner` and `guest` passwords, refresh rotation/replay rejection and access-token revocation.
- Guest password cannot authenticate as owner; guest OAuth subject maps to a real unprivileged Unix UID; owner maps to root only after owner consent.
- The root-owned socket checks the Linux peer UID AND bearer subject/scope. Fake, expired, missing, unmapped or wrong-audience tokens fail. A control-only token cannot transfer files, and a files-only token cannot execute a command.
- Root/unprivileged shell execution, ignored caller-supplied `unix_user` field, denied reading root signing key, separate tmux sessions per Unix UID.
- Per-user text file upload, cross-account storage isolation, signed download retrieval, HMAC tamper/expiry rejection, symlink protection, filename path traversal rejection, denied HTTP/loopback file URL.
- OAuth → MCP → **direct v2 executor** actual root/guest commands, output download and refresh. The original FastAPI is not called by v2.

Local commands used during staging:

```bash
sudo python3 tests/make_fixture.py
# The test executor uses a fixture protected by group permissions.
sudo install -m 0644 tests/test_executor.py /opt/aishell-v2/test_executor.py
sudo -u aishell-mcp-test python3 /opt/aishell-v2/test_executor.py
sudo /opt/aishell-v2/.venv/bin/python tests/test_mcp_shadow.py
```

Tests assume the **staging-only** config and services from this server; they are not run automatically on a fresh installation. Clean synthetic clients/tokens and stop stage services after tests. The staging owner/guest passwords are NEVER committed to this source tree.

## Safe cutover — not yet performed

1. Record and back up current **MCP-only** systemd units, OAuth DB, v2 root config, signing key and nginx virtual host. Preserve a separate SSH/legacy-GPT recovery path.
2. Preserve the existing OAuth registration DB to avoid forcing ChatGPT to recreate its connector; ensure the new OAuth user config maps the existing `local-aishell-owner` subject correctly. Check tokens/scopes and client callback registration.
3. Verify `/opt/aishell-v2/executor.py` and direct v2 service with signed-download loopback checks. Review stale tmux sessions and migration semantics.
4. During a planned short MCP-only cutover, stop `aishell-mcp-test` (port 8766) and activate `aishell-v2-mcp` (same port, issuer and resource). Keep `aishell.service` and `aishell-mcp-bridge` **running** during verification.
5. Add only the `/mcp-v2/download/` nginx location if v2 getFile links should be reachable. Do not replace unrelated `location /`, homepage, applications, static, public, Google OAuth, or legacy APIs. `nginx -t` then reload nginx.
6. Re-test actual ChatGPT Plus OAuth/owner command, guest mode, tmux, file transfer, signed output link. Verify unrelated site endpoints.
7. Rollback: restore MCP-specific nginx configuration, stop new MCP service, restart `aishell-mcp-test`, keep legacy services as before. Preserve OAuth DB backup.
8. Retire the old bridge only after successful real ChatGPT v2 use. **Do not remove the original FastAPI service merely because the old Custom GPT retires; other applications may depend on it.**

## Security limitations requiring further review

- **The MCP OAuth process currently owns its token database.** A full compromise of that process could permit issuing forged privileges because the privileged executor trusts its token records. The local Unix socket and token revalidation defend against accidental tool calls and many lateral accesses, but do not fully isolate a compromised authorization issuer. For a broadly shared service, consider a separate hardened authorization issuer or issuer-signed grants that the web-exposed MCP process cannot mint.
- There is one OAuth password-based identity store here. Production multi-user rollout needs additional controls: per-account login throttling, credential recovery, user deletion/revocation, monitoring, and potentially SSO/MFA rather than shared passwords.
- Tool responses are bounded but root commands can still start background processes and access anything root can. Multi-user safety depends on Unix permissions and careful sudo/group/ACL configuration.
- Pinned dependencies are version-fixed but `requirements.lock` does not yet include package hashes or SBOM. Review upstream updates and security patches before long-term deployment.

## Host-specific notes

The live server is `aishell.gleixner.xyz` running Debian 12. Production (legacy) MCP still routes through nginx at `/mcp` to 127.0.0.1:8766 and an old root bridge which calls FastAPI 127.0.0.1:8000. The **new** staged MCP was tested at 127.0.0.1:8767, executor Unix `/run/aishell-v2-stage/control.sock`, HTTP downloads 127.0.0.1:8781. New standby executor will use `/run/aishell-v2/control.sock` and 127.0.0.1:8780. No nginx or old API route changes are required just to develop or test v2.
