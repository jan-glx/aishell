# aishell v2 — independent MCP + multi-user executor

**Project state (October 12, 2026): v2 is LIVE at `https://aishell.gleixner.xyz/mcp`.** The intermediate MCP 1.9/bridge services are stopped and disabled. The original legacy OpenAPI/FastAPI backend remains active because other server applications depend on it. The ChatGPT OAuth issuer, URL, registration database, and existing grants were retained.

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
| `aishell.service` | Legacy OpenAPI/FastAPI + other applications | **ACTIVE, enabled; preserve** |
| `aishell-v2.service` | Native root-owned multi-user execution service | **ACTIVE, enabled**, Unix socket `/run/aishell-v2/control.sock`, downloads 127.0.0.1:8780 |
| `aishell-v2-mcp.service` | Native MCP/OAuth frontend, 127.0.0.1:8766 | **ACTIVE, enabled**, public `https://aishell.gleixner.xyz/mcp` |
| `aishell-mcp-test.service` | **Obsolete MCP 1.9** compatibility/bridge frontend | **STOPPED, disabled; rollback only** |
| `aishell-mcp-bridge.service` | **Obsolete MCP 1.9** root bridge to FastAPI | **STOPPED, disabled; rollback only** |
| `aishell-v2-stage.service` | Isolated staging executor | STOPPED (tests only) |
| `aishell-v2-mcp-shadow.service` | Isolated staging OAuth/MCP | STOPPED (tests only) |

**Only two application stacks should remain:** (1) legacy FastAPI, which has unrelated application routes; (2) v2 MCP + direct Unix executor. The obsolete MCP 1.9 services are not required for either stack and no longer start at boot.

Public MCP and OAuth discovery, authorization, token, registration and revocation routes keep their original URLs/issuer. Nginx continues to proxy those to port 8766, now owned by `aishell-v2-mcp.service`. The only nginx route added in the v2 deployment is `/mcp-v2/download/` to port 8780, with `access_log off` to avoid logging signed download credentials. **Legacy nginx `/klangwald`, `/nomdebebe`, `/`, `/static`, `/output`, Google OAuth and FastAPI routes were not replaced.**

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
- `/etc/aishell-v2/auth-users.json` root:aishell-mcp-test 0640 — live OAuth usernames, subjects, Argon2id password hashes and scopes. Initially maps `owner` to existing `local-aishell-owner` with the **same verified password hash used by MCP 1.9**; no plaintext credential was copied or committed.
- `/etc/aishell-v2/downloads.key` root:root 0600 — download URL HMAC key.
- `/var/lib/aishell-v2/oauth.sqlite3` — standalone OAuth client/token database for a **new installation**.
- **Current live server uses** `/var/lib/aishell-mcp-test/oauth.sqlite3` — preserved original ChatGPT OAuth registration/tokens, now served by v2 without migrating or replacing the database file. Although the directory name contains `mcp-test`, it is the live state and must not be deleted with the obsolete 1.9 service.
- Staging fixtures: `/var/lib/aishell-v2-stage/oauth.sqlite3` and `/etc/aishell-v2/stage-auth-users.json` — **never copy staging credentials into production**.

## Fresh Debian 12 installation

Prerequisites: an HTTPS virtual host for your domain with working TLS certificates, systemd, `python3-venv`, `tmux`, nginx, a root SSH administration path, and Python 3.11. The installer does NOT set up TLS certificates or unrelated applications.

```bash
sudo apt-get install python3-venv python3-pip tmux
cd /path/to/aishell-v2
sudo AISHELL_DOMAIN=mcp.example.com bash scripts/bootstrap.sh
```

This creates an independent venv, root-owned secrets and config, an MCP service account and systemd files. Nothing starts automatically. It prints the path to the root-only initial owner password. If another OAuth server is already running, set `AISHELL_OAUTH_DB` to the intended database path before running, and review migration implications carefully.

**Review first:** `/etc/aishell-v2/config.json`, `/etc/aishell-v2/auth-users.json`, `/etc/systemd/system/aishell-v2.service`, `/etc/systemd/system/aishell-v2-mcp.service`. The MCP unit template binds port 8766. **Do not start a second MCP process on that port; the production v2 MCP now owns it on this host.** On an empty host, after verifying OAuth/client security and backups, start executor then MCP, integrate *only* the required nginx locations, `nginx -t`, reload nginx, configure private ChatGPT connector with Mixed OAuth and DCR.

### Add a new unprivileged user

```bash
sudo useradd --create-home --shell /bin/bash alice
sudo chmod 700 /home/alice
cd /root/wd/aishell-v2-in-original/mcp-v2
sudo /opt/aishell-v2/.venv/bin/python scripts/add_user.py alice alice
# The script prompts for a NEW, dedicated OAuth password (not Linux login password).
# Review the mapping, then apply the new account lists to both services:
sudo systemctl restart aishell-v2 aishell-v2-mcp
sudo systemctl is-active aishell-v2 aishell-v2-mcp
# Never paste auth-users.json, token DB or password hashes into chat.
```

The command prompts for a separate password, and adds `user:alice` → `alice` to root-owned executor mapping and unprivileged OAuth account mapping. It intentionally refuses to map root or an already-mapped Unix account. It does **not** grant sudo; for strong isolation audit filesystem ACLs, groups, home permissions and any existing sudo policies on the host. Restart the two v2 services deliberately after reviewing configuration changes. There is currently **no production guest password**: `/root/.config/aishell-v2/stage-guest-password` is an isolated throwaway staging identity and does not authenticate on production. The original owner OAuth login still uses the password from `/root/.config/aishell-mcp-oauth/owner-password` until deliberately rotated. Production stores *only account password hashes* in `/etc/aishell-v2/auth-users.json`; users must securely retain their own chosen plaintext password. Password resets and user removal/revocation have not yet been automated.

## Runtime permissions and behavior

- `aishell:ping` — harmless diagnostics.
- `aishell:control` — shell and persistent tmux as mapped UID. Owner root mapping requires explicit `allow_root: true` in root-only config.
- `aishell:files` — per-user file upload to `~/input` and signed download of files in `~/output`.
- Root service verifies valid opaque OAuth token from database (resource, subject, expiry, tool scope) **again**, separately from MCP validation.
- Per-user processes get explicit `HOME`, `USER`, `LOGNAME`, `PATH` and no inherited API credentials. The executor uses OS-level UID/GID drop with empty supplementary group list.
- Shell commands: max 12 KB command text, 65-second time limit, ~70 KB output per stream. Use tmux for long jobs.
- tmux uses the dedicated `-L aishell-v2` socket namespace. **It intentionally does not attach to sessions from the legacy GPT**; migration of old tmux sessions needs a separate operational plan.
- OpenAI file fetches allow only documented OpenAI file-host patterns, use validated public DNS with pinned TLS server hostname and no HTTP redirects. Limit 16 MiB/file. **Genuine ChatGPT-uploaded file IDs/URLs still need an end-to-end user-interface test.**
- Signed files: root executor issues HTTPS URLs with 5-minute HMAC expiration at `/mcp-v2/download/...`. Production nginx now proxies that prefix to localhost:8780 with `access_log off` (do not expose URL signatures in logs). Real signed downloads and tampered-signature rejection were tested over public HTTPS.
- **Inline image content (2026-10-12)**: `getFile` returns the existing signed HTTPS URL in `structuredContent` **and** an MCP `{type:"image", mimeType, data}` content block for validated raster PNG/JPEG/GIF/WebP files in the authenticated user's `~/output` that are at most 512 KiB. This avoids a second DNS/HTTP fetch for image inspection by ChatGPT. The root executor enforces regular-file and `O_NOFOLLOW` protections, verifies the extension and file magic, then reads at most 512 KiB. The MCP frontend strips inline bytes from text and `structuredContent` so the image is transmitted only once. The authorized Unix socket output ceiling is now 1 MiB, while requests, commands and upload limits remain unchanged. Larger images and other files remain URL-only. Full live HTTPS test verified that `iris_pca.png` arrived byte-identically in `ImageContent` and that larger images fall back to URL-only; client-side ChatGPT rendering of that image is still to be confirmed.
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

## Production MCP cutover completed — 2026-10-12

The MCP 1.9 compatibility implementation was a temporary migration shim, not a separate product. There is **no need for an additional domain or ChatGPT connector**. We replaced only the backend behind the existing public `/mcp` endpoint, preserving the owner login and the original OAuth client/token database.

The completed steps were:

1. Made root-only backups of the MCP 1.9 systemd units, original nginx site, live SQLite OAuth database (online consistent SQLite backup), executor config and new auth-users mapping. Recovery directory under `/root/wd/aishell-v2-rollbacks/`, current location recorded in `/root/wd/aishell-v2-rollbacks/LATEST`. **Do not commit or distribute that directory; it contains OAuth grants and password hashes.**
2. Created `/etc/aishell-v2/auth-users.json`, binding `owner` to the same `local-aishell-owner` subject and verified Argon2id password hash. Maintained `/etc/aishell-v2/config.json` root-only Unix UID mapping (`local-aishell-owner` → root).
3. Configured `/etc/systemd/system/aishell-v2-mcp.service` using `AISHELL_V2_ORIGIN=https://aishell.gleixner.xyz`, the **existing** OAuth database `/var/lib/aishell-mcp-test/oauth.sqlite3`, and `/run/aishell-v2/control.sock` for root execution; no legacy FastAPI API token.
4. Tested existing ChatGPT DCR client registration loading in v2 from a backup copy of the real OAuth database.
5. Added ONLY nginx `location ^~ /mcp-v2/download/` to proxy signed output downloads to the root-owned localhost listener 127.0.0.1:8780, with `access_log off`. Verified HTTPS signed-download success and tampered-signature rejection.
6. Stopped `aishell-mcp-test.service`, started `aishell-v2-mcp.service` on the **same port 8766**, and kept `aishell.service` running.
7. Ran full live HTTPS integration: modern MCP `server/discover`, seven-tool discovery, DCR, owner password/consent, PKCE and tokens, ping, direct root `runCommand`, read/send tmux, `transferTextFile`, HTTPS `getFile`, scope denial and token revocation. Later repeated the test after adding inline MCP image content, verifying byte-exact transfer of `iris_pca.png` and URL fallback for files over 512 KiB. Test clients/tokens and temporary files/sessions were removed. The original ChatGPT client registration was preserved.
8. Disabled/stopped both MCP 1.9 services (`aishell-mcp-test.service`, `aishell-mcp-bridge.service`). Enabled v2 executor and MCP service on boot. Verified the old bridge Unix socket is absent while v2 MCP is healthy. Legacy FastAPI remained HTTP 200 at `/openapi.json` and other application routes were checked.

**Rollback (MCP ONLY):**

```bash
# Keep the original FastAPI aishell.service RUNNING; do not undo other applications.
sudo systemctl stop aishell-v2-mcp.service
sudo systemctl enable --now aishell-mcp-bridge.service aishell-mcp-test.service
# Verify:
curl -fsS https://aishell.gleixner.xyz/.well-known/oauth-authorization-server >/dev/null
curl -fsS https://aishell.gleixner.xyz/openapi.json >/dev/null
```

The added signed-download nginx route can be left in place during rollback; it has no effect on legacy routes. If nginx config itself must be restored, use the backed-up *MCP site configuration only* after checking it against later unrelated changes, then run `nginx -t && systemctl reload nginx`. **Do not roll back or delete the live OAuth database unless corruption is confirmed**: v2 and 1.9 use the same DB schema and preserving new tokens/refresh grants avoids unnecessary user reauthorization. Never replace the entire server configuration with a stale snapshot.

**Status to verify separately in the ChatGPT UI:** production HTTPS self-test is successful, but a user-initiated v2 command from the existing ChatGPT plugin after this cutover has not yet been observed. The original connector should continue working without reinstallation. Also, v2 tmux uses a separate `-L aishell-v2` namespace, not the legacy tmux sessions. Genuine attached-file `transferFile` with an OpenAI file service URL needs real client testing.

## Security limitations requiring further review

- **The MCP OAuth process currently owns its token database.** A full compromise of that process could permit issuing forged privileges because the privileged executor trusts its token records. The local Unix socket and token revalidation defend against accidental tool calls and many lateral accesses, but do not fully isolate a compromised authorization issuer. For a broadly shared service, consider a separate hardened authorization issuer or issuer-signed grants that the web-exposed MCP process cannot mint.
- There is one OAuth password-based identity store here. Production multi-user rollout needs additional controls: per-account login throttling, credential recovery, user deletion/revocation, monitoring, and potentially SSO/MFA rather than shared passwords.
- Tool responses are bounded but root commands can still start background processes and access anything root can. Multi-user safety depends on Unix permissions and careful sudo/group/ACL configuration.
- Pinned dependencies are version-fixed but `requirements.lock` does not yet include package hashes or SBOM. Review upstream updates and security patches before long-term deployment.

## Host-specific notes

The live server is `aishell.gleixner.xyz` running Debian 12. Production (legacy) MCP still routes through nginx at `/mcp` to 127.0.0.1:8766 and an old root bridge which calls FastAPI 127.0.0.1:8000. The **new** staged MCP was tested at 127.0.0.1:8767, executor Unix `/run/aishell-v2-stage/control.sock`, HTTP downloads 127.0.0.1:8781. New standby executor will use `/run/aishell-v2/control.sock` and 127.0.0.1:8780. No nginx or old API route changes are required just to develop or test v2.
