from fastapi import FastAPI
from app.routes import upload

app = FastAPI()

app.include_router(upload.router)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

app.mount("/form", StaticFiles(directory=str(Path(__file__).parent), html=True), name="form")

@app.get("/upload-form", response_class=HTMLResponse)
def upload_form():
    return (Path(__file__).parent / "upload_form.html").read_text()
