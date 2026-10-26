from app.auth.routes import get_current_user

from fastapi import Depends, HTTPException, APIRouter, BackgroundTasks, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.targets.models import Target
from app.auth.models import User
from app.kundali.models import Kundali

from app.targets.onboarding import discover_candidate_links, classify_links, normalize_url
from app.kundali.synthesis import build_kundali


router = APIRouter()


def _clean_url_field(v):
    """Normalize URLs dynamically: accepts 'stripe.com' or 'https://stripe.com'."""
    if isinstance(v, str):
        return normalize_url(v)
    return v


class CreateTarget(BaseModel):
    website_url: str

    @field_validator("website_url", mode="before")
    @classmethod
    def normalize_website(cls, v):
        return _clean_url_field(v)


class ConfirmTarget(BaseModel):
    name: str
    website_url: str
    github_url: str | None = None
    ats_url: str | None = None
    blog_url: str | None = None
    web_social_url: str | None = None

    @field_validator("website_url", "github_url", "ats_url", "blog_url", "web_social_url", mode="before")
    @classmethod
    def normalize_all_urls(cls, v):
        return _clean_url_field(v)


def _serialize_kundali(kundali: Kundali, target: Target | None = None) -> dict:
    return {
        "target_id": kundali.target_id,
        "target_name": target.name if target else None,
        "status": target.status if target else None,
        "tech_stack": kundali.tech_stack,
        "cadence_baseline": kundali.cadence_baseline,
        "recent_commits": kundali.recent_commits,
        "ats_postings": kundali.ats_postings,
        "web_social_status": kundali.web_social_status,
        "blog_status": kundali.blog_status,
        "focus_areas": kundali.focus_areas,
        "intelligence": kundali.focus_areas,
        "created_at": kundali.created_at,
    }


@router.post(
    "/preview",
    summary="Step 1: Preview company links",
    description="Discovers and classifies candidate URLs from homepage (e.g. 'stripe.com'). Copy results into Step 2.",
)
def preview(target: CreateTarget, current_user: User = Depends(get_current_user)):
    url = target.website_url
    try:
        links = discover_candidate_links(url)
    except Exception as e:
        raise HTTPException(status_code=405, detail="something went wrong!!!")

    result = classify_links(links)
    return result


@router.post(
    "",
    summary="Step 2: Confirm target & start Day-0 build",
    description="Saves target with confirmed URLs. Use ?sync=true to build immediately and return full Kundali profile.",
)
def confirm(
    target: ConfirmTarget,
    background_tasks: BackgroundTasks,
    sync: bool = Query(
        False,
        description="If true, builds Kundali synchronously and returns the profile immediately for debugging",
    ),
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

    if sync:
        try:
            kundali = build_kundali(
                target_id=new_target.id,
                github_url=new_target.github_url,
                ats_url=new_target.ats_url,
                web_social_url=new_target.web_social_url,
                blog_url=new_target.blog_url,
            )
            db.refresh(new_target)
            return {
                "id": new_target.id,
                "name": new_target.name,
                "status": new_target.status,
                "kundali": _serialize_kundali(kundali, new_target),
            }
        except Exception as e:
            db.refresh(new_target)
            raise HTTPException(
                status_code=500,
                detail=f"Synchronous Kundali build failed: {str(e)}",
            )

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


@router.get(
    "",
    summary="Step 3: List all targets",
    description="Returns all targets created by current user and their current build status ('building', 'ready').",
)
def list_targets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    targets = db.query(Target).filter(Target.user_id == current_user.id).all()
    return targets


@router.get(
    "/{target_id}",
    summary="Step 4: Check target status",
    description="Returns metadata and current build status ('building', 'ready', 'failed') for target_id.",
)
def get_target(target_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.query(Target).filter(Target.id == target_id, Target.user_id == current_user.id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    return target


def _serialize_raw_signals(kundali: Kundali, target: Target | None = None) -> dict:
    return {
        "target_id": kundali.target_id,
        "target_name": target.name if target else None,
        "status": target.status if target else None,
        "created_at": kundali.created_at,
        "tech_stack": kundali.tech_stack,
        "cadence_baseline": kundali.cadence_baseline,
        "recent_commits": kundali.recent_commits,
        "ats_postings": kundali.ats_postings,
        "blog_status": kundali.blog_status,
        "web_social_status": kundali.web_social_status,
    }


def _serialize_synthesis(kundali: Kundali, target: Target | None = None) -> dict:
    return {
        "target_id": kundali.target_id,
        "target_name": target.name if target else None,
        "status": target.status if target else None,
        "created_at": kundali.created_at,
        "intelligence": kundali.focus_areas,
    }


@router.get(
    "/{target_id}/kundali/raw",
    summary="View raw signals (GitHub, ATS, Blog, Social)",
    description="Returns purely uninterpreted empirical signals: language byte counts, 52-week commit cadence, recent commits, open jobs, blog posts, and social updates.",
)
def get_target_kundali_raw(target_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.query(Target).filter(Target.id == target_id, Target.user_id == current_user.id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    kundali = db.query(Kundali).filter(Kundali.target_id == target_id).first()
    if not kundali:
        if target.status == "building":
            return {"status": "building", "message": "Kundali profile is still building in background"}
        raise HTTPException(status_code=404, detail="Kundali profile not found")

    return _serialize_raw_signals(kundali, target)


@router.get(
    "/{target_id}/kundali/synthesis",
    summary="View LLM competitive intelligence dossier",
    description="Returns in-depth strategic analysis: executive summary, strategic focus areas with cross-channel evidence, intelligence correlations, and predictive outlook.",
)
def get_target_kundali_synthesis(target_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.query(Target).filter(Target.id == target_id, Target.user_id == current_user.id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    kundali = db.query(Kundali).filter(Kundali.target_id == target_id).first()
    if not kundali:
        if target.status == "building":
            return {"status": "building", "message": "Kundali profile is still building in background"}
        raise HTTPException(status_code=404, detail="Kundali profile not found")

    return _serialize_synthesis(kundali, target)


@router.get(
    "/{target_id}/kundali",
    summary="View complete unified Kundali",
    description="Returns the full profile combining raw empirical signals with the synthesized intelligence dossier.",
)
def get_target_kundali(target_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.query(Target).filter(Target.id == target_id, Target.user_id == current_user.id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    kundali = db.query(Kundali).filter(Kundali.target_id == target_id).first()
    if not kundali:
        if target.status == "building":
            return {"status": "building", "message": "Kundali profile is still building in background"}
        raise HTTPException(status_code=404, detail="Kundali profile not found")

    return _serialize_kundali(kundali, target)


@router.post(
    "/{target_id}/kundali/build",
    summary="Step 6: Trigger or rebuild Kundali (No body needed)",
    description="Rebuilds Kundali for target_id using its saved URLs directly from database. Just enter target_id.",
)
def build_or_rebuild_kundali(
    target_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = db.query(Target).filter(Target.id == target_id, Target.user_id == current_user.id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    target.status = "building"
    db.commit()

    try:
        kundali = build_kundali(
            target_id=target.id,
            github_url=target.github_url,
            ats_url=target.ats_url,
            web_social_url=target.web_social_url,
            blog_url=target.blog_url,
        )
        db.refresh(target)
        return _serialize_kundali(kundali, target)
    except Exception as e:
        db.refresh(target)
        raise HTTPException(
            status_code=500,
            detail=f"Kundali build failed: {str(e)}",
        )
