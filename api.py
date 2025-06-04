from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import subprocess
import os
from app.routes import upload

app = FastAPI()
app.include_router(upload.router)

API_TOKEN = os.getenv("API_TOKEN", "supersecret")  # Set this securely in env

class CommandRequest(BaseModel):
    command: str

@app.post("/execute")
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

app.mount("/form", StaticFiles(directory=str(Path(__file__).parent), html=True), name="form")

@app.get("/upload-form", response_class=HTMLResponse)
def upload_form():
    return (Path(__file__).parent / "app" / "upload_form.html").read_text()
