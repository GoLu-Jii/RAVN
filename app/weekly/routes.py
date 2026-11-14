"""Owner-scoped weekly analysis API."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.routes import get_current_user
from app.db.database import get_db
from app.weekly.detector import WeeklyRunBusy, run_weekly_analysis
from app.weekly.models import (
    DetectedSignal,
    SignalDetectionRun,
    WeeklyLead,
    WeeklyLeadRun,
)
from app.weekly.repository import (
    get_current_lead_run,
    get_current_signal_detection_run,
    get_owned_target,
    list_current_lead_runs_for_owner,
    list_lead_runs_for_owner,
    list_signal_detection_runs_for_owner,
)
from app.weekly.signal_detector import (
    SignalDetectorBusy,
    WeeklyLeadsUnavailable,
    run_signal_detection,
)
from app.weekly.windows import last_closed_week, require_monday


router = APIRouter(prefix="/targets/{target_id}/weekly", tags=["4. Weekly Leads"])
TIMEZONE = ZoneInfo("Asia/Kolkata")


def _owned_target(target_id: int, user_id: int, db: Session):
    target = get_owned_target(db, target_id, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target not found")
    return target


def _serialize_lead(lead: WeeklyLead) -> dict:
    return {
        "id": lead.id,
        "title": lead.title,
        "description": lead.description,
        "source_event_ids": lead.source_event_ids,
    }


def _serialize_run(run: WeeklyLeadRun, db: Session, include_leads: bool = False) -> dict:
    payload = {
        "id": run.id,
        "target_id": run.target_id,
        "week_start": run.week_start_date,
        "attempt": run.attempt,
        "status": run.status,
        "retryable": run.retryable,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "llm_provider": run.llm_provider,
        "llm_model": run.llm_model,
        "embedding_model_id": run.embedding_model_id,
        "prompt_version": run.prompt_version,
        "input_event_ids": run.input_event_ids,
        "error": run.error,
    }
    if include_leads:
        leads = db.query(WeeklyLead).filter_by(run_id=run.id).order_by(
            WeeklyLead.lead_index.asc()
        ).all()
        payload["leads"] = [_serialize_lead(lead) for lead in leads]
        payload["output"] = run.output_json
    return payload


def _serialize_signal(signal: DetectedSignal) -> dict:
    return {
        "id": signal.id,
        "title": signal.title,
        "description": signal.description,
        "signal_types": signal.signal_types,
        "confidence": signal.confidence,
        "confidence_rationale": signal.confidence_rationale,
        "current_lead_ids": signal.current_lead_ids,
        "historical_lead_ids": signal.historical_lead_ids,
    }


def _serialize_signal_run(
    run: SignalDetectionRun, db: Session, *, include_signals: bool
) -> dict:
    payload = {
        "id": run.id,
        "target_id": run.target_id,
        "week_start": run.week_start_date,
        "attempt": run.attempt,
        "status": run.status,
        "is_current": run.is_current,
        "retryable": run.retryable,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "llm_provider": run.llm_provider,
        "llm_model": run.llm_model,
        "prompt_version": run.prompt_version,
        "input_size_bytes": run.input_size_bytes,
        "error": run.error,
    }
    if include_signals:
        rows = db.query(DetectedSignal).filter_by(run_id=run.id).order_by(
            DetectedSignal.signal_index.asc()
        ).all()
        payload["signals"] = [_serialize_signal(signal) for signal in rows]
        payload["no_matches_reason"] = (
            run.output_json.get("no_matches_reason") if run.output_json else None
        )
    return payload


@router.post("/run", summary="Run weekly lead extraction", description="Runs a single target/week; defaults to the last fully closed Monday-Sunday week.")
def run_weekly(
    target_id: int,
    week_start: date | None = Query(None, description="Monday date (YYYY-MM-DD); defaults to the last closed week"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _owned_target(target_id, current_user.id, db)
    if target.status != "ready":
        raise HTTPException(status_code=409, detail="Target Kundali must be ready before weekly analysis")
    selected_week = week_start or last_closed_week(datetime.now(TIMEZONE))
    try:
        require_monday(selected_week)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return run_weekly_analysis(db, target_id=target_id, week_start=selected_week)
    except WeeklyRunBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/signals/run",
    summary="Run signal detection",
    description="Runs signal analysis for a week with a successful current weekly-lead run.",
)
def run_signals(
    target_id: int,
    week_start: date | None = Query(
        None, description="Monday date (YYYY-MM-DD); defaults to the last closed week"
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _owned_target(target_id, current_user.id, db)
    if target.status != "ready":
        raise HTTPException(status_code=409, detail="Target Kundali must be ready")
    selected_week = week_start or last_closed_week(datetime.now(TIMEZONE))
    try:
        require_monday(selected_week)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return run_signal_detection(
            db, target_id=target_id, week_start=selected_week
        )
    except (SignalDetectorBusy, WeeklyLeadsUnavailable) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{week_start}/signals", summary="Get current weekly signals")
def get_weekly_signals(
    target_id: int,
    week_start: date,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        require_monday(week_start)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if get_owned_target(db, target_id, current_user.id) is None:
        raise HTTPException(status_code=404, detail="Target not found")
    run = get_current_signal_detection_run(db, target_id, week_start)
    if run is None:
        raise HTTPException(status_code=404, detail="Signal detection result not found")
    return _serialize_signal_run(run, db, include_signals=True)


@router.get("/{week_start}/signals/runs", summary="Get signal detection attempts")
def get_signal_runs(
    target_id: int,
    week_start: date,
    include_input: bool = Query(False, description="Include saved signal input/output"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        require_monday(week_start)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    runs = list_signal_detection_runs_for_owner(
        db, target_id, current_user.id, week_start
    )
    if runs is None:
        raise HTTPException(status_code=404, detail="Target not found")
    items = []
    for run in runs:
        item = _serialize_signal_run(run, db, include_signals=False)
        if include_input:
            item.update({
                "input_json": run.input_json,
                "output_json": run.output_json,
                "raw_output": run.raw_output,
            })
        items.append(item)
    return {"target_id": target_id, "week_start": week_start, "items": items}


@router.get("", summary="List weekly lead runs")
def list_weekly_leads(
    target_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runs = list_current_lead_runs_for_owner(db, target_id, current_user.id)
    if runs is None:
        raise HTTPException(status_code=404, detail="Target not found")
    return {
        "target_id": target_id,
        "items": [_serialize_run(run, db, include_leads=True) for run in runs],
    }


@router.get("/{week_start}", summary="Get weekly lead run")
def get_weekly_lead_run(
    target_id: int,
    week_start: date,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        require_monday(week_start)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if get_owned_target(db, target_id, current_user.id) is None:
        raise HTTPException(status_code=404, detail="Target not found")
    run = get_current_lead_run(db, target_id, week_start)
    if run is None:
        raise HTTPException(status_code=404, detail="Weekly lead run not found")
    return _serialize_run(run, db, include_leads=True)


@router.get("/{week_start}/runs", summary="Get weekly run attempts")
def get_weekly_runs(
    target_id: int,
    week_start: date,
    include_input: bool = Query(False, description="Include saved input and LLM output"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        require_monday(week_start)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    runs = list_lead_runs_for_owner(db, target_id, current_user.id, week_start)
    if runs is None:
        raise HTTPException(status_code=404, detail="Target not found")
    items = []
    for run in runs:
        item = {
            "id": run.id,
            "target_id": run.target_id,
            "week_start": run.week_start_date,
            "attempt": run.attempt,
            "status": run.status,
            "is_current": run.is_current,
            "retryable": run.retryable,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "llm_provider": run.llm_provider,
            "llm_model": run.llm_model,
            "embedding_model_id": run.embedding_model_id,
            "prompt_version": run.prompt_version,
            "input_token_estimate": run.input_token_estimate,
            "error": run.error,
        }
        if include_input:
            item.update({
                "input_json": run.input_json,
                "raw_output": run.raw_output,
                "output_json": run.output_json,
            })
        items.append(item)
    return {"target_id": target_id, "week_start": week_start, "items": items}
