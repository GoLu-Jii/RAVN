"""Transaction-safe persistence and owner-scoped lookup helpers for weekly results."""
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy.orm import Session

from app.targets.models import Target
from app.weekly.config import WEEKLY_CONFIG
from app.weekly.models import WeeklyResult, WeeklyRun, WeeklySignal
from app.weekly.models import (
    DetectedSignal,
    SignalDetectionRun,
    WeeklyLead,
    WeeklyLeadRun,
)
from app.weekly.windows import require_monday


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    return value


def create_run(
    db: Session,
    *,
    target_id: int,
    week_start: date,
    attempt: int,
) -> WeeklyRun:
    require_monday(week_start)
    if attempt < 1:
        raise ValueError("attempt must be at least 1.")
    run = WeeklyRun(
        target_id=target_id,
        week_start_date=week_start,
        attempt=attempt,
        status="running",
        retryable=False,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def complete_run(
    db: Session,
    run: WeeklyRun,
    *,
    weekly_input: dict,
    llm_result: dict,
    source_states,
    exact_counts,
    signals: list[dict],
    result_status: str = "ok",
    limited_history: bool | None = None,
) -> WeeklyResult:
    """Atomically mark a run successful and replace that target/week's result."""
    if run.status != "running":
        raise ValueError("Only a running attempt can be completed successfully.")
    if result_status not in {"ok", "low_activity", "no_signals"}:
        raise ValueError("Unsupported weekly result status.")
    output = llm_result.get("output", {})
    metadata = weekly_input.get("metadata", {})
    usage = llm_result.get("usage") or {}

    try:
        result = db.query(WeeklyResult).filter_by(
            target_id=run.target_id,
            week_start_date=run.week_start_date,
        ).first()
        if result is None:
            result = WeeklyResult(
                target_id=run.target_id,
                week_start_date=run.week_start_date,
                source_states=_jsonable(source_states),
                exact_counts=_jsonable(exact_counts),
                status=result_status,
                summary=output.get("summary", ""),
                data_limitations=_jsonable(output.get("data_limitations", [])),
                limited_history=(metadata.get("history_weeks_available", 0) == 0)
                if limited_history is None else limited_history,
            )
            db.add(result)
            db.flush()
        else:
            db.query(WeeklySignal).filter_by(result_id=result.id).delete(synchronize_session=False)
            result.source_states = _jsonable(source_states)
            result.exact_counts = _jsonable(exact_counts)
            result.status = result_status
            result.summary = output.get("summary", "")
            result.data_limitations = _jsonable(output.get("data_limitations", []))
            result.limited_history = (
                metadata.get("history_weeks_available", 0) == 0
                if limited_history is None else limited_history
            )

        run.status = "succeeded"
        run.retryable = False
        run.finished_at = datetime.now(timezone.utc)
        run.llm_provider = llm_result.get("provider")
        run.llm_model = llm_result.get("model")
        run.embedding_model_id = WEEKLY_CONFIG.embed_model_id
        run.prompt_version = llm_result.get("prompt_version") or metadata.get("prompt_version")
        run.prep_version = WEEKLY_CONFIG.prep_version
        run.compaction_level = metadata.get("compaction_level")
        run.input_token_estimate = metadata.get("estimated_tokens")
        run.prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        run.completion_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        run.total_tokens = usage.get("total_tokens")
        run.input_json = _jsonable(weekly_input)
        run.raw_output = llm_result.get("raw_output")
        run.rejected_signals_json = _jsonable(llm_result.get("rejected_signals", []))
        run.error = None
        db.flush()
        result.latest_successful_run_id = run.id
        for signal in signals:
            db.add(WeeklySignal(
                result_id=result.id,
                title=signal["title"],
                description=signal["description"],
                connection_note=signal["connection_note"],
                sources=_jsonable(signal["sources"]),
                signal_types=_jsonable(signal["signal_types"]),
                score=float(signal["score"]),
                configured_sources=int(signal["configured_sources"]),
                confirmed=int(signal["confirmed"]),
                uncertain=int(signal["uncertain"]),
                source_states=_jsonable(signal["source_states"]),
                cited_event_ids=_jsonable(signal["cited_event_ids"]),
                claimed_sources=_jsonable(signal.get("claimed_sources", signal["sources"])),
                source_claim_mismatch=bool(signal.get("source_claim_mismatch", False)),
                uses_first_observed=bool(signal["uses_first_observed"]),
            ))
        db.commit()
        db.refresh(result)
        return result
    except Exception:
        db.rollback()
        raise


def fail_run(
    db: Session,
    run: WeeklyRun,
    *,
    error: str,
    retryable: bool,
    weekly_input: dict | None = None,
    raw_output: str | None = None,
) -> WeeklyRun:
    """Record a failed attempt without touching the current successful result."""
    if run.status != "running":
        raise ValueError("Only a running attempt can be marked failed.")
    run.status = "failed"
    run.retryable = retryable
    run.finished_at = datetime.now(timezone.utc)
    run.error = error
    if weekly_input is not None:
        run.input_json = _jsonable(weekly_input)
        metadata = weekly_input.get("metadata", {})
        run.input_token_estimate = metadata.get("estimated_tokens")
        run.compaction_level = metadata.get("compaction_level")
        run.prompt_version = metadata.get("prompt_version")
    if raw_output is not None:
        run.raw_output = raw_output
    db.commit()
    db.refresh(run)
    return run


def get_owned_target(db: Session, target_id: int, owner_id: int) -> Target | None:
    return db.query(Target).filter(
        Target.id == target_id,
        Target.user_id == owner_id,
    ).first()


def get_result_for_owner(
    db: Session,
    target_id: int,
    owner_id: int,
    week_start: date,
) -> WeeklyResult | None:
    return db.query(WeeklyResult).join(Target, Target.id == WeeklyResult.target_id).filter(
        Target.user_id == owner_id,
        WeeklyResult.target_id == target_id,
        WeeklyResult.week_start_date == week_start,
    ).first()


def list_results_for_owner(db: Session, target_id: int, owner_id: int) -> list[WeeklyResult] | None:
    if get_owned_target(db, target_id, owner_id) is None:
        return None
    return db.query(WeeklyResult).filter_by(target_id=target_id).order_by(
        WeeklyResult.week_start_date.desc()
    ).all()


def list_runs_for_owner(
    db: Session,
    target_id: int,
    owner_id: int,
    week_start: date,
) -> list[WeeklyRun] | None:
    if get_owned_target(db, target_id, owner_id) is None:
        return None
    return db.query(WeeklyRun).filter_by(
        target_id=target_id,
        week_start_date=week_start,
    ).order_by(WeeklyRun.attempt.desc()).all()


def create_lead_run(
    db: Session,
    *,
    target_id: int,
    week_start: date,
    attempt: int,
    input_event_ids: list[int],
    input_json: dict,
    input_token_estimate: int,
) -> WeeklyLeadRun:
    run = WeeklyLeadRun(
        target_id=target_id,
        week_start_date=week_start,
        attempt=attempt,
        status="running",
        input_event_ids=input_event_ids,
        input_json=input_json,
        input_token_estimate=input_token_estimate,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def complete_lead_run(
    db: Session,
    run: WeeklyLeadRun,
    *,
    output_json: dict,
    raw_output: str,
    llm_provider: str | None,
    llm_model: str | None,
    embedding_model_id: str,
    prompt_version: str,
    leads: list[dict],
    vectors: list[list[float]],
) -> WeeklyLeadRun:
    if len(leads) != len(vectors):
        raise ValueError("Lead and embedding counts do not match.")
    db.query(WeeklyLeadRun).filter(
        WeeklyLeadRun.target_id == run.target_id,
        WeeklyLeadRun.week_start_date == run.week_start_date,
        WeeklyLeadRun.is_current.is_(True),
    ).update({WeeklyLeadRun.is_current: False}, synchronize_session=False)
    run.status = "no_events" if not run.input_event_ids else "succeeded"
    run.is_current = True
    run.retryable = False
    run.output_json = output_json
    run.raw_output = raw_output
    run.llm_provider = llm_provider
    run.llm_model = llm_model
    run.embedding_model_id = embedding_model_id
    run.prompt_version = prompt_version
    run.finished_at = datetime.now(timezone.utc)
    run.error = None
    for index, (lead, vector) in enumerate(zip(leads, vectors)):
        db.add(WeeklyLead(
            run_id=run.id,
            target_id=run.target_id,
            week_start_date=run.week_start_date,
            lead_index=index,
            description=lead["description"],
            source_event_ids=lead["source_event_ids"],
            embedding=vector,
            embedding_model_id=embedding_model_id,
        ))
    db.commit()
    db.refresh(run)
    return run


def fail_lead_run(
    db: Session,
    run_id: int,
    *,
    error: str,
    retryable: bool,
    raw_output: str | None = None,
    output_json: dict | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> WeeklyLeadRun:
    run = db.query(WeeklyLeadRun).filter_by(id=run_id).one()
    run.status = "failed"
    run.retryable = retryable
    run.error = error
    run.raw_output = raw_output
    run.output_json = output_json
    if llm_provider:
        run.llm_provider = llm_provider
    if llm_model:
        run.llm_model = llm_model
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def get_current_lead_run(
    db: Session, target_id: int, week_start: date
) -> WeeklyLeadRun | None:
    return db.query(WeeklyLeadRun).filter_by(
        target_id=target_id,
        week_start_date=week_start,
        is_current=True,
    ).first()


def list_lead_runs_for_owner(
    db: Session, target_id: int, owner_id: int, week_start: date
) -> list[WeeklyLeadRun] | None:
    if get_owned_target(db, target_id, owner_id) is None:
        return None
    return db.query(WeeklyLeadRun).filter_by(
        target_id=target_id,
        week_start_date=week_start,
    ).order_by(WeeklyLeadRun.attempt.desc()).all()


def list_current_lead_runs_for_owner(
    db: Session, target_id: int, owner_id: int
) -> list[WeeklyLeadRun] | None:
    if get_owned_target(db, target_id, owner_id) is None:
        return None
    return db.query(WeeklyLeadRun).filter_by(
        target_id=target_id,
        is_current=True,
    ).order_by(WeeklyLeadRun.week_start_date.desc()).all()


def complete_signal_detection_run(
    db: Session,
    run: SignalDetectionRun,
    *,
    output_json: dict,
    raw_output: str,
    llm_provider: str | None,
    llm_model: str | None,
    prompt_version: str,
) -> SignalDetectionRun:
    db.query(SignalDetectionRun).filter(
        SignalDetectionRun.target_id == run.target_id,
        SignalDetectionRun.week_start_date == run.week_start_date,
        SignalDetectionRun.is_current.is_(True),
    ).update({SignalDetectionRun.is_current: False}, synchronize_session=False)
    signals = output_json["signals"]
    run.status = "succeeded" if signals else "no_signals"
    run.is_current = True
    run.retryable = False
    run.output_json = output_json
    run.raw_output = raw_output
    run.llm_provider = llm_provider
    run.llm_model = llm_model
    run.prompt_version = prompt_version
    run.finished_at = datetime.now(timezone.utc)
    run.error = None
    for index, signal in enumerate(signals):
        db.add(DetectedSignal(
            run_id=run.id,
            target_id=run.target_id,
            week_start_date=run.week_start_date,
            signal_index=index,
            title=signal["title"],
            description=signal["description"],
            signal_types=signal["signal_types"],
            confidence=signal["confidence"],
            confidence_rationale=signal["confidence_rationale"],
            current_lead_ids=signal["current_lead_ids"],
            historical_lead_ids=signal["historical_lead_ids"],
        ))
    db.commit()
    db.refresh(run)
    return run


def fail_signal_detection_run(
    db: Session,
    run_id: int,
    *,
    error: str,
    retryable: bool,
    raw_output: str | None = None,
    output_json: dict | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> SignalDetectionRun:
    run = db.query(SignalDetectionRun).filter_by(id=run_id).one()
    run.status = "failed"
    run.retryable = retryable
    run.error = error
    run.raw_output = raw_output
    run.output_json = output_json
    if llm_provider:
        run.llm_provider = llm_provider
    if llm_model:
        run.llm_model = llm_model
    run.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def get_current_signal_detection_run(
    db: Session, target_id: int, week_start: date
) -> SignalDetectionRun | None:
    return db.query(SignalDetectionRun).filter_by(
        target_id=target_id,
        week_start_date=week_start,
        is_current=True,
    ).first()


def list_signal_detection_runs_for_owner(
    db: Session,
    target_id: int,
    owner_id: int,
    week_start: date,
) -> list[SignalDetectionRun] | None:
    if get_owned_target(db, target_id, owner_id) is None:
        return None
    return db.query(SignalDetectionRun).filter_by(
        target_id=target_id,
        week_start_date=week_start,
    ).order_by(SignalDetectionRun.attempt.desc()).all()
