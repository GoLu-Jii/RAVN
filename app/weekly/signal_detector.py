"""Signal detection over current weekly leads, Kundali, and prior lead vectors."""
from datetime import date, timedelta
import json
from math import isfinite, sqrt
from threading import Lock

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.embeddings.constants import EMBEDDING_DIMENSION
from app.kundali.models import Kundali
from app.targets.models import Target
from app.weekly.config import WEEKLY_CONFIG, WeeklyConfig
from app.weekly.models import (
    SignalDetectionRun,
    WeeklyLead,
    WeeklyLeadRun,
)
from app.weekly.repository import (
    complete_signal_detection_run,
    fail_signal_detection_run,
)
from app.weekly.signal_extraction import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    SignalExtraction,
    SignalExtractionError,
    extract_signals,
)
from app.weekly.windows import require_monday


SIGNAL_DETECTOR_LOCK = Lock()
HISTORY_WEEKS = 12
HISTORICAL_LEADS_PER_CURRENT = 3


class SignalDetectorBusy(RuntimeError):
    pass


class WeeklyLeadsUnavailable(ValueError):
    pass


class SignalInputError(ValueError):
    pass


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != EMBEDDING_DIMENSION or len(right) != EMBEDDING_DIMENSION:
        raise SignalInputError(
            f"Weekly lead vectors must have {EMBEDDING_DIMENSION} dimensions."
        )
    if not all(isfinite(value) for value in left + right):
        raise SignalInputError("Weekly lead vectors contain non-finite values.")
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        raise SignalInputError("Weekly lead vectors must have non-zero norms.")
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _history_filters(target_id: int, week_start: date, model_id: str):
    return (
        WeeklyLead.target_id == target_id,
        WeeklyLead.week_start_date >= week_start - timedelta(weeks=HISTORY_WEEKS),
        WeeklyLead.week_start_date < week_start,
        WeeklyLead.embedding_model_id == model_id,
        WeeklyLeadRun.is_current.is_(True),
        WeeklyLeadRun.status.in_(("succeeded", "no_events")),
    )


def _retrieve_historical_leads(
    db: Session,
    *,
    target_id: int,
    week_start: date,
    current_leads: list[WeeklyLead],
    model_id: str,
) -> list[dict]:
    """Retrieve top-three prior-week lead candidates per current lead and deduplicate."""
    if not current_leads:
        return []

    filters = _history_filters(target_id, week_start, model_id)
    selected: dict[int, dict] = {}
    if db.get_bind().dialect.name == "postgresql":
        for current in current_leads:
            distance = WeeklyLead.embedding.cosine_distance(current.embedding)
            rows = db.execute(
                select(WeeklyLead, distance.label("distance"))
                .join(WeeklyLeadRun, WeeklyLeadRun.id == WeeklyLead.run_id)
                .where(*filters)
                .order_by(distance.asc(), WeeklyLead.id.asc())
                .limit(HISTORICAL_LEADS_PER_CURRENT)
            ).all()
            matches = [
                (lead, 1.0 - float(distance_value))
                for lead, distance_value in rows
            ]
            _merge_history_matches(selected, current.id, matches)
    else:
        history = db.query(WeeklyLead).join(
            WeeklyLeadRun, WeeklyLeadRun.id == WeeklyLead.run_id
        ).filter(*filters).all()
        for current in current_leads:
            ranked = [
                (lead, _cosine_similarity(current.embedding, lead.embedding))
                for lead in history
            ]
            ranked.sort(key=lambda item: (-item[1], item[0].id))
            _merge_history_matches(
                selected,
                current.id,
                ranked[:HISTORICAL_LEADS_PER_CURRENT],
            )

    candidates = []
    for item in selected.values():
        candidates.append(item)
    candidates.sort(
        key=lambda item: (
            -item["week_start_date"].toordinal(),
            -item["similarity"],
            item["id"],
        )
    )
    return [
        {
            "id": item["id"],
            "week_start_date": item["week_start_date"].isoformat(),
            "title": item["title"],
            "description": item["description"],
            "similarity": round(item["similarity"], 6),
            "matched_current_lead_ids": sorted(item["matched_current_lead_ids"]),
        }
        for item in candidates
    ]


def _merge_history_matches(
    selected: dict[int, dict],
    current_lead_id: int,
    matches: list[tuple[WeeklyLead, float]],
) -> None:
    for lead, similarity in matches:
        item = selected.get(lead.id)
        if item is None:
            item = {
                "id": lead.id,
                "week_start_date": lead.week_start_date,
                "title": lead.title,
                "description": lead.description,
                "similarity": similarity,
                "matched_current_lead_ids": set(),
            }
            selected[lead.id] = item
        item["similarity"] = max(item["similarity"], similarity)
        item["matched_current_lead_ids"].add(current_lead_id)


def _build_signal_input(
    *,
    target: Target,
    week_start: date,
    kundali: Kundali,
    current_leads: list[WeeklyLead],
    historical_candidates: list[dict],
) -> dict:
    return {
        "target": {"id": target.id, "name": target.name},
        "week_start": week_start.isoformat(),
        "kundali": {
            "tech_stack": kundali.tech_stack,
            "focus_areas": kundali.focus_areas,
            "cadence_baseline": kundali.cadence_baseline,
            "recent_commits": kundali.recent_commits,
            "ats_postings": kundali.ats_postings,
            "web_social_status": kundali.web_social_status,
            "blog_status": kundali.blog_status,
        },
        "current_leads": [
            {
                "id": lead.id,
                "title": lead.title,
                "description": lead.description,
                "source_event_ids": lead.source_event_ids,
            }
            for lead in current_leads
        ],
        "historical_candidates": historical_candidates,
    }


def _fit_signal_input(
    signal_input: dict,
    *,
    config: WeeklyConfig,
) -> tuple[dict, int]:
    """Preserve Kundali/current leads and drop lower-priority old history to fit."""
    candidates = list(signal_input["historical_candidates"])
    base_input = {**signal_input, "historical_candidates": []}
    base_text = json.dumps(base_input, ensure_ascii=False)
    base_size = len((SYSTEM_PROMPT + base_text).encode("utf-8"))
    if base_size > config.signal_input_byte_budget:
        raise SignalInputError(
            "Kundali and current-week leads exceed the Signal Detector's input limit."
        )

    fitted = {**signal_input, "historical_candidates": candidates}
    serialized = json.dumps(fitted, ensure_ascii=False)
    size = len((SYSTEM_PROMPT + serialized).encode("utf-8"))
    while size > config.signal_input_byte_budget and candidates:
        candidates.pop()
        fitted = {**signal_input, "historical_candidates": list(candidates)}
        serialized = json.dumps(fitted, ensure_ascii=False)
        size = len((SYSTEM_PROMPT + serialized).encode("utf-8"))
    return fitted, size


def _current_weekly_leads(
    db: Session, target_id: int, week_start: date
) -> tuple[WeeklyLeadRun, list[WeeklyLead]]:
    run = db.query(WeeklyLeadRun).filter_by(
        target_id=target_id,
        week_start_date=week_start,
        is_current=True,
    ).first()
    if run is None or run.status not in {"succeeded", "no_events"}:
        raise WeeklyLeadsUnavailable(
            "Weekly lead extraction has not successfully completed for this week."
        )
    leads = db.query(WeeklyLead).filter_by(run_id=run.id).order_by(
        WeeklyLead.lead_index.asc()
    ).all()
    return run, leads


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (SignalExtractionError,)):
        return exc.retryable
    return not isinstance(exc, (ValueError, TypeError))


def run_signal_detection(
    db: Session,
    *,
    target_id: int,
    week_start: date,
    config: WeeklyConfig = WEEKLY_CONFIG,
    llm_client=None,
) -> dict:
    """Detect signals from one week's leads and prior same-target lead vectors."""
    require_monday(week_start)
    if not SIGNAL_DETECTOR_LOCK.acquire(blocking=False):
        raise SignalDetectorBusy("A Signal Detector run is already active in this process.")

    run_id = None
    extraction: SignalExtraction | None = None
    try:
        target = db.query(Target).filter_by(id=target_id).first()
        if target is None:
            raise ValueError("target_not_found")
        if target.status != "ready":
            raise ValueError("target_not_ready")
        _, current_leads = _current_weekly_leads(db, target_id, week_start)
        attempt = db.query(SignalDetectionRun).filter_by(
            target_id=target_id,
            week_start_date=week_start,
        ).count() + 1
        run = SignalDetectionRun(
            target_id=target_id,
            week_start_date=week_start,
            attempt=attempt,
            status="running",
            is_current=False,
            retryable=False,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

        kundali = db.query(Kundali).filter_by(target_id=target_id).first()
        if kundali is None:
            raise ValueError("kundali_not_found")
        historical = _retrieve_historical_leads(
            db,
            target_id=target_id,
            week_start=week_start,
            current_leads=current_leads,
            model_id=config.embed_model_id,
        )
        signal_input, input_size = _fit_signal_input(
            _build_signal_input(
                target=target,
                week_start=week_start,
                kundali=kundali,
                current_leads=current_leads,
                historical_candidates=historical,
            ),
            config=config,
        )
        run.input_json = signal_input
        run.input_size_bytes = input_size
        db.commit()

        extraction = extract_signals(
            signal_input,
            config=config,
            llm_client=llm_client,
        )
        completed = complete_signal_detection_run(
            db,
            run,
            output_json=extraction.output,
            raw_output=extraction.raw_output,
            llm_provider=extraction.provider,
            llm_model=extraction.model,
            prompt_version=PROMPT_VERSION,
        )
        return {
            "status": completed.status,
            "target_id": completed.target_id,
            "week_start": completed.week_start_date.isoformat(),
            "run_id": completed.id,
            "attempt": completed.attempt,
            "signal_count": len(extraction.output["signals"]),
            "input_size_bytes": completed.input_size_bytes,
            "retryable": False,
        }
    except Exception as exc:
        if run_id is None:
            raise
        retryable = _retryable(exc)
        db.rollback()
        failure_extraction = exc if isinstance(exc, SignalExtractionError) else None
        failed = fail_signal_detection_run(
            db,
            run_id,
            error=f"{type(exc).__name__}: {exc}",
            retryable=retryable,
            raw_output=(
                extraction.raw_output if extraction
                else failure_extraction.raw_output if failure_extraction
                else None
            ),
            output_json=extraction.output if extraction else None,
            llm_provider=(
                extraction.provider if extraction
                else failure_extraction.provider if failure_extraction
                else None
            ),
            llm_model=(
                extraction.model if extraction
                else failure_extraction.model if failure_extraction
                else None
            ),
        )
        return {
            "status": failed.status,
            "target_id": failed.target_id,
            "week_start": failed.week_start_date.isoformat(),
            "run_id": failed.id,
            "attempt": failed.attempt,
            "retryable": failed.retryable,
            "error": failed.error,
        }
    finally:
        SIGNAL_DETECTOR_LOCK.release()

