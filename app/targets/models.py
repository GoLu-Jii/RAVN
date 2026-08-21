from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    website_url: Mapped[str] = mapped_column(String, nullable=False)
    github_url: Mapped[Optional[str]] = mapped_column(String)
    ats_url: Mapped[Optional[str]] = mapped_column(String)
    blog_url: Mapped[Optional[str]] = mapped_column(String)
    web_social_url: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="building", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())