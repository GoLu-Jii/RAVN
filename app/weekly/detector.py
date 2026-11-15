"""Weekly extraction, embedding, and persistence orchestration."""
from datetime import date, timedelta
import json
from threading import Lock

from sqlalchemy.orm import Session

from app.embeddings.model import EmbeddingProviderError, embed_texts
from app.events.models import Event
from app.targets.models import Target
from app.weekly.config import WEEKLY_CONFIG, WeeklyConfig
from app.weekly.lead_extraction import (
    PROMPT_VERSION,
    WeeklyLeadExtractionError,
    build_weekly_lead_input,
    extract_weekly_leads_batched,
)
from app.weekly.models import WeeklyLeadRun
from app.weekly.repository import (
    complete_lead_run,
    create_lead_run,
    fail_lead_run,
)
from app.weekly.windows import require_monday, week_window


WEEKLY_LOCK = Lock()


class WeeklyRunBusy(RuntimeError):
    pass


def _failure_is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (WeeklyLeadExtractionError, EmbeddingProviderError)):
        return exc.retryable
    return not isinstance(exc, (ValueError, TypeError))


def run_weekly_analysis(
    db: Session,
    *,
    target_id: int,
    week_start: date,
    config: WeeklyConfig = WEEKLY_CONFIG,
    embedder=None,
    llm_client=None,
) -> dict:
    """Extract all supported leads from one Monday-Sunday collection window."""
    require_monday(week_start)
    if not WEEKLY_LOCK.acquire(blocking=False):
        raise WeeklyRunBusy("A weekly run is already active in this process.")
    run_id = None
    extraction = None
    try:
        target = db.query(Target).filter_by(id=target_id).first()
        if target is None:
            raise ValueError("target_not_found")
        if target.status != "ready":
            raise ValueError("target_not_ready")

        window = week_window(week_start)
        events = db.query(Event).filter(
            Event.target_id == target_id,
            Event.collection_date >= window.start_date,
            Event.collection_date < window.end_date + timedelta(days=1),
            Event.is_baseline.is_(False),
        ).order_by(Event.collection_date.asc(), Event.id.asc()).all()
        weekly_input = build_weekly_lead_input(week_start, events)
        serialized_input = json.dumps(weekly_input, ensure_ascii=False)
        attempt = db.query(WeeklyLeadRun).filter_by(
            target_id=target_id,
            week_start_date=week_start,
        ).count() + 1
        run = create_lead_run(
            db,
            target_id=target_id,
            week_start=week_start,
            attempt=attempt,
            input_event_ids=[event.id for event in events],
            input_json=weekly_input,
            input_token_estimate=max(1, (len(serialized_input.encode("utf-8")) + 3) // 4),
        )
        run_id = run.id

        extraction = extract_weekly_leads_batched(
            weekly_input,
            config=config,
            llm_client=llm_client,
        )
        leads = extraction.output["leads"] or []
        texts = [f'{lead["title"]}\n{lead["description"]}' for lead in leads]
        vectors = (embedder or embed_texts)(texts) if texts else []
        if len(vectors) != len(leads):
            raise EmbeddingProviderError(
                "The embedding provider returned a different number of vectors than leads."
            )
        completed = complete_lead_run(
            db,
            run,
            output_json=extraction.output,
            raw_output=extraction.raw_output,
            llm_provider=extraction.provider,
            llm_model=extraction.model,
            embedding_model_id=config.embed_model_id,
            prompt_version=PROMPT_VERSION,
            leads=leads,
            vectors=vectors,
        )
        return {
            "status": completed.status,
            "target_id": completed.target_id,
            "week_start": completed.week_start_date.isoformat(),
            "run_id": completed.id,
            "attempt": completed.attempt,
            "lead_count": len(leads),
            "batch_count": extraction.batch_count,
            "retryable": False,
        }
    except Exception as exc:
        if run_id is None:
            raise
        retryable = _failure_is_retryable(exc)
        extraction_error = exc if isinstance(exc, WeeklyLeadExtractionError) else None
        db.rollback()
        failed = fail_lead_run(
            db,
            run_id,
            error=f"{type(exc).__name__}: {exc}",
            retryable=retryable,
            raw_output=(
                extraction.raw_output if extraction
                else extraction_error.raw_output if extraction_error
                else None
            ),
            output_json=extraction.output if extraction else None,
            llm_provider=(
                extraction.provider if extraction
                else extraction_error.provider if extraction_error
                else None
            ),
            llm_model=(
                extraction.model if extraction
                else extraction_error.model if extraction_error
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
        WEEKLY_LOCK.release()
