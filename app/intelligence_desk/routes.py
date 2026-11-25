"""Owner-scoped Company Scout run and published briefing endpoints."""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.db.database import SessionLocal, get_db
from app.intelligence_desk.agents.company_scout import run_company_scout
from app.intelligence_desk.models import IntelligenceBriefing, IntelligenceRun
from app.targets.models import Target
from app.weekly.models import WeeklyLead, WeeklyLeadRun
from app.weekly.windows import last_closed_week, require_monday


router = APIRouter(prefix="/api/targets/{target_id}", tags=["5. Intelligence"])


def _owned_target(db: Session, target_id: int, user_id: int) -> Target:
    target = db.query(Target).filter_by(id=target_id, user_id=user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Target not found")
    return target


def _message_text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def _execution_result(agent_state: dict) -> dict:
    """Keep the final answer and lightweight tool-call trace, not raw page text."""
    messages = agent_state.get("messages", [])
    trace = []
    for message in messages:
        for call in getattr(message, "tool_calls", []) or []:
            trace.append({"type": "tool_call", "name": call.get("name"), "args": call.get("args", {})})
        if getattr(message, "type", None) == "tool":
            trace.append({"type": "tool_result", "name": getattr(message, "name", None), "status": "completed"})
    final_output = _message_text(messages[-1]) if messages else ""
    return {"final_output": final_output, "trace": trace}


def _execute_run(run_id: int, target_id: int, company_name: str, website_url: str,
                 week_start: date, week_end: date, weekly_leads: list[dict],
                 lead_context_status: str) -> None:
    db = SessionLocal()
    try:
        run = db.query(IntelligenceRun).filter_by(id=run_id, target_id=target_id).first()
        if run is None:
            return
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()

    try:
        state = run_company_scout(
            target_id=target_id,
            company_name=company_name,
            website_url=website_url,
            week_start=week_start.isoformat(),
            week_end=week_end.isoformat(),
            weekly_leads=weekly_leads,
            lead_context_status=lead_context_status,
        )
        result = _execution_result(state)
        db = SessionLocal()
        try:
            run = db.query(IntelligenceRun).filter_by(id=run_id, target_id=target_id).first()
            if run is not None:
                run.status = "succeeded"
                run.result = result
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()
    except Exception as exc:
        db = SessionLocal()
        try:
            run = db.query(IntelligenceRun).filter_by(id=run_id, target_id=target_id).first()
            if run is not None:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"[:4000]
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()


@router.post("/intelligence/run", status_code=status.HTTP_202_ACCEPTED,
             summary="Start a Company Scout intelligence run")
def start_intelligence_run(
    target_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _owned_target(db, target_id, current_user.id)
    if target.status != "ready":
        raise HTTPException(status_code=409, detail="Target Kundali must be ready before intelligence research")

    week_start = last_closed_week()
    require_monday(week_start)
    week_end = week_start + timedelta(days=6)

    lead_run = db.query(WeeklyLeadRun).filter_by(
        target_id=target_id, week_start_date=week_start, is_current=True
    ).first()
    weekly_leads = []
    lead_context_status = "unavailable"
    if lead_run is not None and lead_run.status in {"succeeded", "no_events"}:
        lead_context_status = "available"
        lead_rows = db.query(WeeklyLead).filter_by(run_id=lead_run.id).order_by(
            WeeklyLead.lead_index.asc()
        ).all()
        weekly_leads = [{
            "id": item.id,
            "description": item.description,
            "source_event_ids": item.source_event_ids,
        } for item in lead_rows]

    run = IntelligenceRun(
        target_id=target_id,
        week_start_date=week_start,
        week_end_date=week_end,
        status="queued",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(
        _execute_run, run.id, target_id, target.name, target.website_url,
        week_start, week_end, weekly_leads, lead_context_status
    )
    return {
        "run_id": run.id,
        "target_id": target_id,
        "week_start": week_start,
        "week_end": week_end,
        "status": run.status,
    }


@router.get("/intelligence/runs/{run_id}", summary="Get Company Scout run status and result")
def get_intelligence_run(
    target_id: int,
    run_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _owned_target(db, target_id, current_user.id)
    run = db.query(IntelligenceRun).filter_by(id=run_id, target_id=target_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Intelligence run not found")
    return {
        "run_id": run.id,
        "target_id": run.target_id,
        "week_start": run.week_start_date,
        "week_end": run.week_end_date,
        "status": run.status,
        "result": run.result,
        "error": run.error,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


@router.get("/briefings", summary="List published Company Scout briefings")
def list_published_briefings(
    target_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _owned_target(db, target_id, current_user.id)
    briefings = db.query(IntelligenceBriefing).filter_by(
        target_id=target_id, status="published"
    ).order_by(IntelligenceBriefing.week_start_date.desc()).offset(offset).limit(limit).all()
    return {
        "target_id": target_id,
        "items": [{
            "id": item.id,
            "week_start": item.week_start_date,
            "week_end": item.week_end_date,
            "status": item.status,
            "content": item.content,
            "published_at": item.published_at,
        } for item in briefings],
        "limit": limit,
        "offset": offset,
    }
