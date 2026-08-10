from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pathlib import Path
from .auth import verify_token

router = APIRouter(dependencies=[Depends(verify_token)])

def save_direct_upload(
    filename: str = Form(None),
    content: str = Form(None),
    file: UploadFile = File(None)
) -> JSONResponse:
    input_path = Path.home() / "input"
    input_path.mkdir(exist_ok=True)

    if file:
        file_location = input_path / file.filename
        with open(file_location, "wb") as f:
            f.write(file.file.read())
        saved_filename = file.filename

    elif filename and content:
        file_location = input_path / filename
        with open(file_location, "w") as f:
            f.write(content)
        saved_filename = filename

    else:
        raise HTTPException(status_code=400, detail="No valid file or content provided")

    return JSONResponse({
        "filename": saved_filename,
        "path": f"~/input/{saved_filename}"
    })


@router.post("/transfer/form")
def transfer_file(
    filename: str = Form(None),
    content: str = Form(None),
    file: UploadFile = File(None)
):
    return save_direct_upload(filename=filename, content=content, file=file)


@router.post("/upload")
def upload_file(file: UploadFile = File(...)):
    return save_direct_upload(file=file)
