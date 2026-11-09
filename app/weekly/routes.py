"""Owner-scoped weekly analysis API."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.routes import get_current_user
from app.db.database import get_db
from app.weekly.detector import WeeklyRunBusy, run_weekly_analysis
from app.weekly.models import WeeklySignal
from app.weekly.repository import (
    get_owned_target,
    get_result_for_owner,
    list_results_for_owner,
    list_runs_for_owner,
)
from app.weekly.windows import last_closed_week, require_monday


router = APIRouter(prefix="/targets/{target_id}/weekly", tags=["4. Weekly Analysis"])
TIMEZONE = ZoneInfo("Asia/Kolkata")


def _owned_target(target_id: int, user_id: int, db: Session):
    target = get_owned_target(db, target_id, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target not found")
    return target


def _serialize_signal(signal: WeeklySignal) -> dict:
    return {
        "id": signal.id,
        "title": signal.title,
        "description": signal.description,
        "connection_note": signal.connection_note,
        "sources": signal.sources,
        "signal_types": signal.signal_types,
        "score": signal.score,
        "configured_sources": signal.configured_sources,
        "confirmed": signal.confirmed,
        "uncertain": signal.uncertain,
        "source_states": signal.source_states,
        "cited_event_ids": signal.cited_event_ids,
        "claimed_sources": signal.claimed_sources,
        "source_claim_mismatch": signal.source_claim_mismatch,
        "uses_first_observed": signal.uses_first_observed,
    }


def _serialize_result(result, db: Session, include_signals: bool = False) -> dict:
    payload = {
        "id": result.id,
        "target_id": result.target_id,
        "week_start": result.week_start_date,
        "latest_successful_run_id": result.latest_successful_run_id,
        "source_states": result.source_states,
        "exact_counts": result.exact_counts,
        "status": result.status,
        "summary": result.summary,
        "data_limitations": result.data_limitations,
        "limited_history": result.limited_history,
        "updated_at": result.updated_at,
    }
    if include_signals:
        signals = db.query(WeeklySignal).filter_by(result_id=result.id).order_by(WeeklySignal.id.asc()).all()
        payload["signals"] = [_serialize_signal(signal) for signal in signals]
    return payload


@router.post("/run", summary="Run weekly analysis", description="Runs a single target/week; defaults to the last fully closed Monday-Sunday week.")
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


@router.get("", summary="List weekly results")
def list_weekly_results(
    target_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    results = list_results_for_owner(db, target_id, current_user.id)
    if results is None:
        raise HTTPException(status_code=404, detail="Target not found")
    return {
        "target_id": target_id,
        "items": [_serialize_result(result, db) for result in results],
    }


@router.get("/{week_start}", summary="Get weekly result and signals")
def get_weekly_result(
    target_id: int,
    week_start: date,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        require_monday(week_start)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = get_result_for_owner(db, target_id, current_user.id, week_start)
    if result is None:
        raise HTTPException(status_code=404, detail="Weekly result not found")
    return _serialize_result(result, db, include_signals=True)


@router.get("/{week_start}/runs", summary="Get weekly run attempts")
def get_weekly_runs(
    target_id: int,
    week_start: date,
    include_input: bool = Query(False, description="Include saved input/output and rejection details"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        require_monday(week_start)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    runs = list_runs_for_owner(db, target_id, current_user.id, week_start)
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
            "retryable": run.retryable,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "llm_provider": run.llm_provider,
            "llm_model": run.llm_model,
            "embedding_model_id": run.embedding_model_id,
            "prompt_version": run.prompt_version,
            "prep_version": run.prep_version,
            "compaction_level": run.compaction_level,
            "input_token_estimate": run.input_token_estimate,
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            "total_tokens": run.total_tokens,
            "error": run.error,
        }
        if include_input:
            item.update({
                "input_json": run.input_json,
                "raw_output": run.raw_output,
                "rejected_signals": run.rejected_signals_json,
            })
        items.append(item)
    return {"target_id": target_id, "week_start": week_start, "items": items}
