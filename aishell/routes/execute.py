from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
import subprocess
import os

router = APIRouter()

API_TOKEN = os.getenv("API_TOKEN", "supersecret")

class CommandRequest(BaseModel):
    command: str

@router.post("/execute")
def execute_command(request: CommandRequest, authorization: str = Header(None)):
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        env = {**os.environ,
            "HOME": os.environ.get("HOME", "/root"),
            "USER": os.environ.get("USER", "root"),
            "LOGNAME": os.environ.get("LOGNAME", "root"),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp")
        }
        result = subprocess.run(
            ["bash", "-l", "-c", request.command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
