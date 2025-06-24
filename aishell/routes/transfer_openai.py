from fastapi import Header, HTTPException, APIRouter
from pydantic import BaseModel
from typing import List
import requests
from pathlib import Path
import os

router = APIRouter()
API_TOKEN = os.getenv("API_TOKEN", "supersecret")

class OpenAIFile(BaseModel):
    name: str
    id: str
    mime_type: str
    download_link: str

class TransferRequest(BaseModel):
    openaiFileIdRefs: List[OpenAIFile]

@router.post("/transfer")
def transfer_file(
    data: TransferRequest,
    authorization: str = Header(None)
):
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    output_path = Path.home() / "output"
    output_path.mkdir(exist_ok=True)

    saved_urls = []
    for f in data.openaiFileIdRefs:
        r = requests.get(f.download_link, timeout=10)
        if r.status_code == 200:
            file_path = output_path / f.name
            with open(file_path, "wb") as out_file:
                out_file.write(r.content)
            saved_urls.append(f"https://shell.gleixner.xyz/output/{f.name}")
        else:
            raise HTTPException(status_code=502, detail=f"Download failed: {f.name}")

    return {"urls": saved_urls}
