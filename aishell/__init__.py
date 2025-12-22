from fastapi import FastAPI, HTTPException, Header
from .logging_middleware import LoggingMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import subprocess
import os
from .routes import upload, tmux, transfer_openai, transfer_endpoint, download_endpoint, execute, log 

app = FastAPI()
app.add_middleware(LoggingMiddleware)
app.include_router(upload.router)
app.include_router(tmux.router)

API_TOKEN = os.getenv("API_TOKEN", "supersecret")  # Set this securely in env

class CommandRequest(BaseModel):
    command: str

app.include_router(transfer_openai.router)
app.include_router(transfer_endpoint.router)
app.include_router(download_endpoint.router)
app.include_router(log.router)
app.include_router(execute.router)
