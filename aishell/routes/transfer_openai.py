from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
import requests
from pathlib import Path
from .auth import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])

class OpenAIFile(BaseModel):
    name: str
    id: str
    mime_type: str
    download_link: str

class TransferRequest(BaseModel):
    openaiFileIdRefs: List[OpenAIFile]

@router.post("/transfer")
def transfer_file(data: TransferRequest):
    input_path = Path.home() / "input"
    input_path.mkdir(exist_ok=True)

    files = []
    for f in data.openaiFileIdRefs:
        print("Starting upload for", f.name)
        try:
            r = requests.get(f.download_link, timeout=10)
            r.raise_for_status()
        except Exception as e:
            print(f"Download failed for {f.name}: {e}")
            raise HTTPException(status_code=502, detail=f"Download failed: {f.name}")

        file_path = input_path / f.name
        with open(file_path, "wb") as out_file:
            out_file.write(r.content)

        files.append({
            "filename": f.name,
            "path": f"~/input/{f.name}"
        })

    return {"files": files}
