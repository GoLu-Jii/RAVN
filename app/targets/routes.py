from app.auth.routes import get_current_user

from fastapi import Depends, HTTPException, APIRouter, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.base import Base
from app.db.database import get_db
from app.targets.models import Target
from app.auth.models import User

from app.targets.onboarding import discover_candidate_links, classify_links
from app.kundali.synthesis import build_kundali



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




from app.kundali.models import Kundali


@router.post("")
def confirm(
    target: ConfirmTarget,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    new_target = Target(
        user_id=current_user.id,
        name=target.name,
        website_url=target.website_url,
        github_url=target.github_url,
        ats_url=target.ats_url,
        blog_url=target.blog_url,
        web_social_url=target.web_social_url,
        status="building",
    )
    db.add(new_target)
    db.commit()
    db.refresh(new_target)

    # Launch Day 0 Kundali building in background
    background_tasks.add_task(
        build_kundali,
        target_id=new_target.id,
        github_url=new_target.github_url,
        ats_url=new_target.ats_url,
        web_social_url=new_target.web_social_url,
        blog_url=new_target.blog_url,
    )

    return {"id": new_target.id, "name": new_target.name, "status": new_target.status}


@router.get("")
def list_targets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    targets = db.query(Target).filter(Target.user_id == current_user.id).all()
    return targets


@router.get("/{target_id}")
def get_target(target_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.query(Target).filter(Target.id == target_id, Target.user_id == current_user.id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    return target


@router.get("/{target_id}/kundali")
def get_target_kundali(target_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.query(Target).filter(Target.id == target_id, Target.user_id == current_user.id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    kundali = db.query(Kundali).filter(Kundali.target_id == target_id).first()
    if not kundali:
        if target.status == "building":
            return {"status": "building", "message": "Kundali profile is still building in background"}
        raise HTTPException(status_code=404, detail="Kundali profile not found")

    return {
        "target_id": kundali.target_id,
        "status": target.status,
        "tech_stack": kundali.tech_stack,
        "cadence_baseline": kundali.cadence_baseline,
        "recent_commits": kundali.recent_commits,
        "ats_postings": kundali.ats_postings,
        "web_social_status": kundali.web_social_status,
        "blog_status": kundali.blog_status,
        "focus_areas": kundali.focus_areas,
        "created_at": kundali.created_at,
    }


