from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
import subprocess
import time

router = APIRouter()

def ensure_tmux_session(session: str):
    result = subprocess.run(["tmux", "has-session", "-t", session], stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        subprocess.run(["tmux", "new-session", "-d", "-s", session], check=True)

@router.get("/tmux/{session}")
def read_tmux_buffer(session: str):
    try:
        ensure_tmux_session(session)
        result = subprocess.run(
            ["tmux", "capture-pane", "-pt", session],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return {"output": result.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class InputModel(BaseModel):
    input: list[str]

@router.post("/tmux/{session}/send")
def send_tmux_keys(session: str, body: InputModel):
    try:
        ensure_tmux_session(session)
        subprocess.run(
            ["tmux", "send-keys", "-t", session, *body.input],
            check=True
        )
        time.sleep(0.3)
        result = subprocess.run(
            ["tmux", "capture-pane", "-pt", session],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return {"output": result.stdout}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=404, detail=str(e))
