"""Owner-scoped Company Scout run and published briefing endpoints."""

import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

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
REPORTING_TZ = ZoneInfo("Asia/Kolkata")


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


def _tool_payload(message):
    content = getattr(message, "content", "")
    if isinstance(content, (dict, list)):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (TypeError, ValueError):
            return {"text": content[:300]}
    return {"text": str(content)[:300]}


def _compact_tool_result(message) -> dict:
    payload = _tool_payload(message)
    if not isinstance(payload, dict):
        return {"type": "tool_result", "name": getattr(message, "name", None), "status": "unknown"}

    result = {
        "type": "tool_result",
        "name": getattr(message, "name", None),
        "status": payload.get("status", "unknown"),
    }
    for key in ("url", "title", "published_at", "count", "diagnostic", "pages_checked", "pages_read", "cached", "truncated", "language", "language_code", "video_id"):
        if key in payload:
            result[key] = payload[key]

    # Keep useful provenance in the run trace without persisting whole pages/transcripts.
    if isinstance(payload.get("links"), list):
        result["links"] = [
            {"url": item.get("url"), "text": item.get("text", "")[:160]}
            for item in payload["links"] if isinstance(item, dict) and item.get("url")
        ]
    if isinstance(payload.get("items"), list):
        result["items"] = [
            {key: item[key] for key in ("video_id", "title", "url", "published_at") if key in item}
            for item in payload["items"] if isinstance(item, dict)
        ]
    return result


def _publication_day(value) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.date()
    return parsed.astimezone(REPORTING_TZ).date()


def _url_key(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", parsed.query, ""))


def _review_output(final_output: str, trace: list[dict], week_start: date, week_end: date) -> dict:
    """Annotate findings for review; never reject or rewrite the model's answer."""
    try:
        parsed = json.loads(final_output)
    except (TypeError, ValueError):
        return {"json_parsed": False, "findings": []}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("findings", []), list):
        return {"json_parsed": True, "findings": [], "note": "Expected a findings list to review."}

    checked_urls = set()
    for item in trace:
        if item.get("type") == "tool_result" and item.get("status") in {"checked", "partial"}:
            if item.get("url"):
                checked_urls.add(_url_key(item["url"]))
            for video in item.get("items", []):
                if video.get("url"):
                    checked_urls.add(_url_key(video["url"]))

    reviewed = []
    for index, finding in enumerate(parsed.get("findings", [])):
        if not isinstance(finding, dict):
            reviewed.append({"finding_index": index, "date_status": "unknown", "citation_status": []})
            continue
        published_day = _publication_day(finding.get("published_at"))
        if published_day is None:
            date_status = "unknown"
        elif week_start <= published_day <= week_end:
            date_status = "in_period"
        else:
            date_status = "outside_period"
        citations = finding.get("source_urls", [])
        if isinstance(citations, str):
            citations = [citations]
        citation_status = []
        for citation in citations if isinstance(citations, list) else []:
            citation_status.append({
                "url": citation,
                "status": "returned_by_checked_tool" if isinstance(citation, str) and _url_key(citation) in checked_urls else "not_seen_in_checked_tool_results",
            })
        reviewed.append({"finding_index": index, "date_status": date_status, "citation_status": citation_status})
    return {"json_parsed": True, "findings": reviewed}


def _execution_result(agent_state: dict, week_start: date | None = None, week_end: date | None = None) -> dict:
    """Keep the final answer, compact tool trace, and non-blocking review annotations."""
    messages = agent_state.get("messages", [])
    trace = []
    for message in messages:
        for call in getattr(message, "tool_calls", []) or []:
            trace.append({"type": "tool_call", "name": call.get("name"), "args": call.get("args", {})})
        if getattr(message, "type", None) == "ai":
            metadata = getattr(message, "response_metadata", {}) or {}
            model_name = metadata.get("model_name") or metadata.get("model")
            if model_name:
                trace.append({
                    "type": "model_response",
                    "model": model_name,
                    "provider": metadata.get("model_provider"),
                })
        if getattr(message, "type", None) == "tool":
            trace.append(_compact_tool_result(message))
    final_output = _message_text(messages[-1]) if messages else ""
    return {
        "final_output": final_output,
        "trace": trace,
        "review": _review_output(final_output, trace, week_start, week_end)
        if week_start and week_end else None,
    }


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
        result = _execution_result(state, week_start, week_end)
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
