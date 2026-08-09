from app.auth.routes import get_current_user

from fastapi import Depends, HTTPException, APIRouter
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.base import Base
from app.db.database import get_db
from app.targets.models import Target
from app.auth.models import User

from app.targets.onboarding import discover_candidate_links, classify_links



router = APIRouter()

class CreateTarget(BaseModel):
    website_url : str

class ConfirmTarget(BaseModel):
    name: str
    website_url: str
    github_url: str | None = None
    ats_url: str | None = None
    blog_url: str | None = None
    web_social_url: str | None = None




@router.post("/preview")
def preview(target: CreateTarget, current_user: User = Depends(get_current_user)):
    url = target.website_url
    try:
        links = discover_candidate_links(url)
    except Exception as e:
        raise HTTPException(status_code=405,detail= "something went wrong!!!")
    

    result = classify_links(links)
    return result




@router.post("")
def confirm(target: ConfirmTarget, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    new_target = Target(
        user_id = current_user.id,
        name = target.name,
        website_url = target.website_url,
        github_url = target.github_url,
        ats_url = target.ats_url,
        blog_url = target.blog_url,
        web_social_url = target.web_social_url
    )
    db.add(new_target)
    db.commit()
    db.refresh(new_target)

    return {"id": new_target.id, "name": new_target.name, "status": new_target.status}