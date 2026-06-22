import os
import secrets

from fastapi import HTTPException, Header, status


def get_api_token() -> str:
    token = os.getenv("API_TOKEN")
    if not token:
        raise RuntimeError("API_TOKEN is not set")
    return token


def verify_token(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token format")

    if not secrets.compare_digest(token, get_api_token()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
