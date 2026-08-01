from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.database import get_db
from app.auth.models import User
from app.auth.utils import hash_password, verify_access_token, verify_password, create_access_token


router = APIRouter()

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
