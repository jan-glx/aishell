from fastapi import File, UploadFile, Form, Header, HTTPException, APIRouter
from fastapi.responses import JSONResponse
from pathlib import Path
import os

router = APIRouter()
API_TOKEN = os.getenv("API_TOKEN", "supersecret")

@router.post("/transfer")
def transfer_file(
    authorization: str = Header(None),
    filename: str = Form(None),
    content: str = Form(None),
    file: UploadFile = File(None)
):
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    output_path = Path.home() / "output"
    output_path.mkdir(exist_ok=True)

    if file:
        file_location = output_path / file.filename
        with open(file_location, "wb") as f:
            f.write(file.file.read())
        saved_filename = file.filename

    elif filename and content:
        file_location = output_path / filename
        with open(file_location, "w") as f:
            f.write(content)
        saved_filename = filename

    else:
        raise HTTPException(status_code=400, detail="No valid file or content provided")

    return JSONResponse({
        "url": f"https://shell.gleixner.xyz/output/{saved_filename}"
    })
