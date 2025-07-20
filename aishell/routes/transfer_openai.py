from fastapi import Header, HTTPException, APIRouter
from pydantic import BaseModel
from typing import List
import requests
from pathlib import Path
import os
from openai import OpenAI

router = APIRouter()
API_TOKEN = os.getenv("API_TOKEN", "supersecret")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set")

client = OpenAI(api_key=OPENAI_API_KEY)

class OpenAIFile(BaseModel):
    name: str
    id: str
    mime_type: str
    download_link: str

class TransferRequest(BaseModel):
    openaiFileIdRefs: List[OpenAIFile]

@router.post("/transfer")
def transfer_file(data: TransferRequest, authorization: str = Header(None)):
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    output_path = Path.home() / "output"
    output_path.mkdir(exist_ok=True)

    file_ids = []
    for f in data.openaiFileIdRefs:
        r = requests.get(f.download_link, timeout=10)
        if r.status_code == 200:
            file_path = output_path / f.name
            with open(file_path, "wb") as out_file:
                out_file.write(r.content)

            with open(file_path, "rb") as upload_file:
                result = client.files.create(file=upload_file, purpose="assistants")
                file_ids.append(result.id)
        else:
            raise HTTPException(status_code=502, detail=f"Download failed: {f.name}")

    return {"file_ids": file_ids}
