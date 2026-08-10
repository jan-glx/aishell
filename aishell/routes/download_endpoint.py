import hashlib
import hmac
import mimetypes
import os
import secrets
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse

from .auth import get_api_token, verify_token

DOWNLOAD_URL_TTL_SECONDS = 300

router = APIRouter(dependencies=[Depends(verify_token)])
public_router = APIRouter()


def _file_path(filename: str) -> Path:
    if Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="File not found")
    return Path.home() / "output" / filename


def _download_signature(filename: str, expires: int) -> str:
    payload = f"{filename}\n{expires}".encode("utf-8")
    return hmac.new(
        get_api_token().encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


@router.get("/download")
@router.get("/download/{filename}", include_in_schema=False)
def get_file(filename: str):
    file_path = _file_path(filename)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    domain = os.getenv("YOUR_DOMAIN")
    if not domain:
        raise HTTPException(status_code=500, detail="YOUR_DOMAIN is not set")

    expires = int(time.time()) + DOWNLOAD_URL_TTL_SECONDS
    signature = _download_signature(filename, expires)
    query = urlencode({"expires": expires, "signature": signature})
    download_url = (
        f"https://{domain}/download-file/{quote(filename, safe='')}?{query}"
    )

    return JSONResponse({"openaiFileResponse": [download_url]})


@public_router.get("/download-file/{filename}", include_in_schema=False)
@public_router.head("/download-file/{filename}", include_in_schema=False)
def download_file(filename: str, expires: int, signature: str):
    expected_signature = _download_signature(filename, expires)
    if expires < int(time.time()) or not secrets.compare_digest(
        signature, expected_signature
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired download URL",
        )

    file_path = _file_path(filename)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(
        file_path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment",
        headers={"Cache-Control": "no-store"},
    )
