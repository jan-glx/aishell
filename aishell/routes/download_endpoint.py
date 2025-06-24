from fastapi import HTTPException, APIRouter
from fastapi.responses import FileResponse
from pathlib import Path

router = APIRouter()

@router.get("/download/{filename}")
def get_file(filename: str):
    file_path = Path.home() / "output" / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        file_path,
        media_type="application/octet-stream",
        filename=filename
    )
