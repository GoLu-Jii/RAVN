"""End-to-end weekly analysis orchestration."""
from datetime import date, timedelta
import json
from threading import Lock

from sqlalchemy.orm import Session

from app.embeddings.service import embed_missing_events
from app.events.models import Event
from app.weekly.config import WEEKLY_CONFIG, WeeklyConfig
from app.weekly.input_builder import WeeklyInputError, build_weekly_input
from app.weekly.llm_stage import WeeklyLLMError, run_weekly_llm
from app.weekly.models import WeeklyRun
from app.weekly.prompt import PROMPT_VERSION
from app.weekly.repository import complete_run, create_run, fail_run
from app.weekly.scoring import enrich_verified_signal
from app.weekly.verify import verify_signals
from app.weekly.windows import require_monday, week_window


WEEKLY_LOCK = Lock()


class WeeklyRunBusy(RuntimeError):
    pass


def _state_map(source_rows: list[dict]) -> dict:
    result = {}
    for row in source_rows:
        result[row["source"]] = {key: value for key, value in row.items() if key != "source"}
    return result


def _summary(status: str, weekly_input: dict) -> str:
    counts = weekly_input["weekly_counts"][-1]["sources"]
    source_rows = {row["source"]: row for row in weekly_input["sources"]}
    count_text = ", ".join(f"{source} {counts[source]}" for source in counts)
    coverage_text = ", ".join(
        f"{source} {source_rows[source]['coverage']}" for source in counts
    )
    if status == "low_activity":
        return f"Low activity this week: no eligible events. Counts: {count_text}. Coverage: {coverage_text}."
    return f"No verified signals this week. Counts: {count_text}. Coverage: {coverage_text}."


def _failure_response(db: Session, run: WeeklyRun, exc: Exception, weekly_input: dict | None = None) -> dict:
    if isinstance(exc, WeeklyLLMError):
        retryable = exc.retryable
        raw_output = exc.raw_output
        if exc.provider:
            run.llm_provider = exc.provider
        if exc.model:
            run.llm_model = exc.model
    else:
        retryable = not isinstance(exc, (WeeklyInputError, ValueError, TypeError))
        raw_output = None
    fail_run(
        db,
        run,
        error=f"{type(exc).__name__}: {exc}",
        retryable=retryable,
        weekly_input=weekly_input,
        raw_output=raw_output,
    )
    return {
        "status": "failed",
        "target_id": run.target_id,
        "week_start": run.week_start_date.isoformat(),
        "run_id": run.id,
        "attempt": run.attempt,
        "retryable": retryable,
        "error": str(exc),
    }


def run_weekly_analysis(
    db: Session,
    *,
    target_id: int,
    week_start: date,
    config: WeeklyConfig = WEEKLY_CONFIG,
    embedder=None,
    llm_client=None,
) -> dict:
    """Run the complete pipeline once; callers schedule retryable failures."""
    require_monday(week_start)
    if not WEEKLY_LOCK.acquire(blocking=False):
        raise WeeklyRunBusy("A weekly run is already active in this process.")
    try:
        from app.targets.models import Target

        target = db.query(Target).filter_by(id=target_id).first()
        if target is None:
            raise ValueError("target_not_found")
        if target.status != "ready":
            raise ValueError("target_not_ready")
        attempt = db.query(WeeklyRun).filter_by(
            target_id=target_id,
            week_start_date=week_start,
        ).count() + 1
        run = create_run(db, target_id=target_id, week_start=week_start, attempt=attempt)
        weekly_input = None
        try:
            embedding_report = embed_missing_events(db, target_id=target_id, embedder=embedder)
            if embedding_report["status"] in {"failed", "partial"}:
                raise RuntimeError(
                    f"Embedding step {embedding_report['status']}: {embedding_report.get('error_type', 'unknown error')}"
                )

            weekly_input = build_weekly_input(
                db,
                target_id=target_id,
                week_start=week_start,
                prompt_version=PROMPT_VERSION,
                config=config,
            )
            current_events = weekly_input["current_week_events"]
            source_states = _state_map(weekly_input["sources"])
            exact_counts = weekly_input["weekly_counts"]
            rejected_signals = []

            if not current_events:
                result_status = "low_activity"
                summary = _summary(result_status, weekly_input)
                llm_result = {
                    "output": {"summary": summary, "data_limitations": []},
                    "raw_output": json.dumps({"summary": summary, "signals": []}),
                    "provider": None,
                    "model": None,
                    "prompt_version": PROMPT_VERSION,
                    "usage": None,
                }
                verified_signals = []
            else:
                llm_result = run_weekly_llm(
                    weekly_input,
                    client=llm_client,
                    config=config,
                    prompt_version=PROMPT_VERSION,
                )
                persisted_events = db.query(Event).filter(
                    Event.target_id == target_id,
                    Event.collection_date >= week_start - timedelta(weeks=config.history_weeks),
                    Event.collection_date <= week_window(week_start).end_date,
                ).all()
                verification = verify_signals(
                    llm_result["output"]["signals"],
                    persisted_events,
                    target_id=target_id,
                    week_start=week_start,
                    history_weeks=config.history_weeks,
                )
                rejected_signals = verification["rejected_signals"]
                verified_signals = [
                    enrich_verified_signal(
                        signal,
                        source_states,
                        week_start=week_start,
                        trend_min_weeks=config.trend_min_weeks,
                    )
                    for signal in verification["verified_signals"]
                ]
                llm_result = {**llm_result, "rejected_signals": rejected_signals}
                if verified_signals:
                    result_status = "ok"
                else:
                    result_status = "no_signals"
                    llm_result["output"] = {
                        **llm_result["output"],
                        "summary": _summary(result_status, weekly_input),
                    }

            result = complete_run(
                db,
                run,
                weekly_input=weekly_input,
                llm_result=llm_result,
                source_states=source_states,
                exact_counts=exact_counts,
                signals=verified_signals,
                result_status=result_status,
            )
            return {
                "status": result.status,
                "target_id": target_id,
                "week_start": week_start.isoformat(),
                "result_id": result.id,
                "run_id": run.id,
                "attempt": run.attempt,
                "summary": result.summary,
                "signal_count": len(verified_signals),
                "rejected_signal_count": len(rejected_signals),
                "limited_history": result.limited_history,
                "input_token_estimate": run.input_token_estimate,
            }
        except Exception as exc:
            return _failure_response(db, run, exc, weekly_input)
    finally:
        WEEKLY_LOCK.release()
