from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from pathlib import Path
import json, os, hmac, time

router = APIRouter()
CFG = Path.home() / '.config/google-workspace'

@router.get('/google/oauth2/callback', response_class=HTMLResponse)
async def google_oauth_callback(state: str = Query(...), code: str = Query(None), error: str = Query(None)):
    pending = CFG / 'pending_oauth.json'
    if not pending.exists():
        return HTMLResponse('<h2>No OAuth authorization is pending.</h2>', status_code=400)
    try:
        meta = json.loads(pending.read_text())
    except Exception:
        return HTMLResponse('<h2>OAuth state could not be read.</h2>', status_code=500)
    if not hmac.compare_digest(state, meta.get('state','')):
        return HTMLResponse('<h2>OAuth state mismatch.</h2>', status_code=400)
    if time.time() - meta.get('created', 0) > 900:
        return HTMLResponse('<h2>OAuth request expired. Please start again.</h2>', status_code=400)
    result = {'state': state, 'code': code, 'error': error, 'received': time.time()}
    out = CFG / 'oauth_callback.json'
    out.write_text(json.dumps(result))
    os.chmod(out, 0o600)
    if error:
        return HTMLResponse(f'<h2>Google authorization failed: {error}</h2><p>You may close this tab.</p>', status_code=400)
    return HTMLResponse('<h2>Google authorization received.</h2><p>aishell is completing setup. You may close this tab.</p>')
