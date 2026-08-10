from fastapi import FastAPI
from .logging_middleware import LoggingMiddleware
from pydantic import BaseModel
from .routes import tmux, transfer_openai, transfer_endpoint, download_endpoint, execute, log, google_oauth

app = FastAPI()
app.add_middleware(LoggingMiddleware)
app.include_router(tmux.router)

class CommandRequest(BaseModel):
    command: str

app.include_router(transfer_openai.router)
app.include_router(transfer_endpoint.router)
app.include_router(download_endpoint.router)
app.include_router(download_endpoint.public_router)
app.include_router(log.router)
app.include_router(execute.router)

app.include_router(google_oauth.router)
