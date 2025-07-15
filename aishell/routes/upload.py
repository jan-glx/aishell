import os
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status
from pathlib import Path

router = APIRouter()

OUTPUT_DIR = Path("/root/uploads")
OUTPUT_DIR.mkdir(exist_ok=True)

API_TOKEN = os.getenv("API_TOKEN", "")

@router.post("/upload")
async def upload_file(token: str = Form(...), file: UploadFile = File(...)):
    if token != API_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    file_path = OUTPUT_DIR / file.filename
    with file_path.open("wb") as buffer:
        content = await file.read()
        buffer.write(content)
    return {"filename": file.filename, "url": f"https://aishell.gleixner.xyz/uploads/{file.filename}"}
