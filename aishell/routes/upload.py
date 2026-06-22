from fastapi import APIRouter, Depends, UploadFile, File
from pathlib import Path
from .auth import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])

OUTPUT_DIR = Path.home() / "uploads"
OUTPUT_DIR.mkdir(exist_ok=True)

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    file_path = OUTPUT_DIR / file.filename
    with file_path.open("wb") as buffer:
        content = await file.read()
        buffer.write(content)
    return {"filename": file.filename, "url": f"https://aishell.gleixner.xyz/uploads/{file.filename}"}
