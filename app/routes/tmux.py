from fastapi import APIRouter, HTTPException
import subprocess

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

@router.post("/tmux/{session}/send")
def send_tmux_keys(session: str, input: str):
    try:
        ensure_tmux_session(session)
        subprocess.run(
            ["tmux", "send-keys", "-t", session, input, "C-m"],
            check=True
        )
        return {"status": "sent", "input": input}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=404, detail=str(e))
