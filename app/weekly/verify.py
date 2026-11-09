"""Code verification for model-proposed weekly signal citations."""
from datetime import date, timedelta

from app.weekly.windows import require_monday


SOURCES = ("github", "ats", "blog", "web_social")


def _value(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _source_order(sources) -> list[str]:
    unique = set(sources)
    return [source for source in SOURCES if source in unique]


def verify_signals(
    candidates,
    events,
    *,
    target_id: int,
    week_start: date,
    history_weeks: int = 12,
) -> dict:
    """Separate accepted verified signals from rejected model proposals."""
    require_monday(week_start)
    if history_weeks < 1:
        raise ValueError("history_weeks must be at least one.")
    week_end = week_start + timedelta(days=6)
    history_start = week_start - timedelta(weeks=history_weeks)
    event_by_id = {_value(event, "id", _value(event, "event_id")): event for event in events}
    accepted = []
    rejected = []

    for index, candidate in enumerate(candidates):
        claimed_sources = _source_order(_value(candidate, "sources", []) or [])
        raw_ids = _value(candidate, "cited_event_ids", []) or []
        cited_ids = list(dict.fromkeys(raw_ids))
        reasons = []
        cited_events = []
        for event_id in cited_ids:
            event = event_by_id.get(event_id)
            if event is None:
                reasons.append("unknown_event_id")
                continue
            if _value(event, "target_id") != target_id:
                reasons.append("wrong_target")
                continue
            if bool(_value(event, "is_baseline", False)):
                reasons.append("baseline_event")
                continue
            event_day = _value(event, "collection_date")
            if not isinstance(event_day, date) or not history_start <= event_day <= week_end:
                reasons.append("event_outside_allowed_window")
                continue
            source = _value(event, "source_type")
            if source not in claimed_sources:
                reasons.append("cited_source_not_claimed")
                continue
            cited_events.append(event)

        if not any(
            week_start <= _value(event, "collection_date") <= week_end
            for event in cited_events
        ):
            reasons.append("no_current_week_citation")
        reasons = list(dict.fromkeys(reasons))
        actual_sources = _source_order(_value(event, "source_type") for event in cited_events)
        source_mismatch = actual_sources != claimed_sources

        result = {
            "title": _value(candidate, "title", ""),
            "description": _value(candidate, "description", ""),
            "connection_note": _value(candidate, "connection_note", ""),
            "sources": actual_sources,
            "claimed_sources": claimed_sources,
            "source_claim_mismatch": source_mismatch,
            "cited_event_ids": cited_ids,
            "uses_first_observed": any(
                _value(event, "date_basis") == "first_observed" for event in cited_events
            ),
            "_verified_events": cited_events,
        }
        if reasons:
            if isinstance(candidate, dict):
                candidate_data = candidate
            elif hasattr(candidate, "model_dump"):
                candidate_data = candidate.model_dump()
            elif hasattr(candidate, "dict"):
                candidate_data = candidate.dict()
            else:
                candidate_data = vars(candidate)
            rejected.append({
                "candidate_index": index,
                "candidate": candidate_data,
                "rejection_reasons": reasons,
            })
        else:
            accepted.append(result)

    return {"verified_signals": accepted, "rejected_signals": rejected}
