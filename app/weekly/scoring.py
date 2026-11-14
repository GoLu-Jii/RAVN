"""Deterministic source support scores and signal labels."""
from datetime import date, timedelta

from app.weekly.verify import SOURCES, _value
from app.weekly.windows import require_monday


SOURCE_SCORE = {
    "supported": 1.0,
    "checked_none": 0.0,
    "uncertain": 0.5,
    "not_configured": 0.0,
}


def score_signal(verified_events, source_statuses) -> dict:
    """Score each source from verified event support and coverage only."""
    cited_sources = {_value(event, "source_type") for event in verified_events}
    states = {}
    configured_sources = 0
    confirmed = 0
    uncertain = 0
    total = 0.0

    for source in SOURCES:
        status = source_statuses[source]
        configured = bool(_value(status, "configured", False))
        coverage = _value(status, "coverage", status if isinstance(status, str) else None)
        if not configured:
            state = "not_configured"
        else:
            configured_sources += 1
            if source in cited_sources:
                state = "supported"
                confirmed += 1
            elif coverage == "complete":
                state = "checked_none"
            elif coverage in {"partial", "unavailable"}:
                state = "uncertain"
                uncertain += 1
            else:
                raise ValueError(f"Configured source {source} has unsupported coverage state {coverage!r}.")
        value = SOURCE_SCORE[state]
        total += value
        states[source] = {"state": state, "score": value}

    return {
        "score": total,
        "configured_sources": configured_sources,
        "confirmed": confirmed,
        "uncertain": uncertain,
        "source_states": states,
    }


def signal_types(verified_events, week_start: date, trend_min_weeks: int = 3) -> list[str]:
    """Derive spike/trend/isolated labels from verified citations only."""
    require_monday(week_start)
    if trend_min_weeks < 2:
        raise ValueError("trend_min_weeks must be at least two.")
    week_end = week_start + timedelta(days=6)
    current_sources = {
        _value(event, "source_type")
        for event in verified_events
        if week_start <= _value(event, "collection_date") <= week_end
    }
    citation_weeks = {
        _value(event, "collection_date") - timedelta(
            days=_value(event, "collection_date").weekday()
        )
        for event in verified_events
    }
    labels = []
    if len(current_sources) >= 2:
        labels.append("spike")
    if len(citation_weeks) >= trend_min_weeks:
        labels.append("trend")
    return labels or ["isolated"]


def enrich_verified_signal(
    signal: dict,
    source_statuses,
    *,
    week_start: date,
    trend_min_weeks: int = 3,
) -> dict:
    """Attach code-derived score and type; ignore model-proposed score/type keys."""
    verified_events = signal["_verified_events"]
    enriched = {
        key: value for key, value in signal.items()
        if key not in {"_verified_events", "score", "signal_types", "signal_type"}
    }
    enriched.update(score_signal(verified_events, source_statuses))
    enriched["signal_types"] = signal_types(
        verified_events,
        week_start,
        trend_min_weeks=trend_min_weeks,
    )
    return enriched
