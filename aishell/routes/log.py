from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
import os
from .auth import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])
LOG_PATH = "/var/log/aishell/api_log.jsonl"

def tail(filename, n=10, buf_size=1024):
    lines = []
    with open(filename, "rb") as f:
        f.seek(0, 2)  # go to end of file
        file_size = f.tell()
        block_end = file_size
        while len(lines) <= n and block_end > 0:
            block_start = max(0, block_end - buf_size)
            f.seek(block_start)
            block_data = f.read(block_end - block_start)
            lines = block_data.split(b"\n") + lines
            block_end = block_start
    return b"\n".join(lines[-n:]).decode("utf-8", errors="replace")

@router.get("/api/log", response_class=PlainTextResponse)
async def get_log():
    if not os.path.exists(LOG_PATH):
        raise HTTPException(status_code=404, detail="Log file not found")
    with open(LOG_PATH, "r") as f:
        return f.read()
