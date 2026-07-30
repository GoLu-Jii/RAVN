from sqlalchemy import Integer, String, DateTime, func
from sqlalchemy.orm import mapped_column

from app.db.base import Base

class User(Base):
    __tablename__ = "users"
    id = mapped_column(Integer, primary_key=True)
    email = mapped_column(String, unique=True, nullable=False)
    password_hash = mapped_column(String, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())