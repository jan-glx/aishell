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
        result = subprocess.run(
            request.command,
            executable="/bin/bash",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
