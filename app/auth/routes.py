from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from fastapi.security import OAuth2PasswordBearer
import jwt

from app.db.database import get_db
from app.auth.models import User
from app.auth.utils import hash_password, verify_access_token, verify_password, create_access_token


router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

class CreateUser(BaseModel):
    email: str
    password: str

@router.post("/signup")
def signup(user: CreateUser, db: Session = Depends(get_db)):

    existing_user = db.query(User).filter(User.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User is already registered")

    hashed_password = hash_password(user.password)

    new_user = User(
        email = user.email,
        password_hash = hashed_password
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {"id": new_user.id,
            "email": new_user.email}



@router.post("/login")
def login(user: CreateUser, db: Session = Depends(get_db)):

    existing_user = db.query(User).filter(User.email == user.email).first()

    if not existing_user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    else:
        if(verify_password(user.password, existing_user.password_hash)):
            access_token = create_access_token(str(existing_user.id))
        else:
            raise HTTPException(status_code=401, detail="Invalid email or password")

    return {"access_token": access_token, "token_type": "bearer"}



@router.get("/me")
def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = verify_access_token(token)
    except jwt.exceptions.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.exceptions.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token")

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="user not found")
    return {"id": user.id, "email": user.email}