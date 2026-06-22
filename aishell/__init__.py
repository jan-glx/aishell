from fastapi import FastAPI
from .logging_middleware import LoggingMiddleware
from pydantic import BaseModel
from .routes import upload, tmux, transfer_openai, transfer_endpoint, download_endpoint, execute, log 

app = FastAPI()
app.add_middleware(LoggingMiddleware)
app.include_router(upload.router)
app.include_router(tmux.router)

class CommandRequest(BaseModel):
    command: str

app.include_router(transfer_openai.router)
app.include_router(transfer_endpoint.router)
app.include_router(download_endpoint.router)
app.include_router(log.router)
app.include_router(execute.router)
