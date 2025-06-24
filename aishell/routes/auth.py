from fastapi import Depends, HTTPException, Header, status

API_TOKEN = "your_secure_token_here"  # Replace with your actual token

def verify_token(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token format")
    token = authorization.split(" ")[1]
    if token != API_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
