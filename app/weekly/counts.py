"""Exact event counts by source for the current and preceding collection weeks."""
from datetime import date
from typing import Iterable

from app.weekly.coverage import SOURCES
from app.weekly.windows import previous_week_starts, require_monday, week_start_for


def _value(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def weekly_event_counts(
    events: Iterable,
    week_start: date,
    history_weeks: int = 12,
) -> dict[date, dict[str, int]]:
    """Count non-baseline events for each source, oldest history week through current week."""
    require_monday(week_start)
    week_starts = previous_week_starts(week_start, history_weeks) + [week_start]
    counts = {start: {source: 0 for source in SOURCES} for start in week_starts}
    included_weeks = set(counts)

    for event in events:
        if bool(_value(event, "is_baseline", False)):
            continue
        source = _value(event, "source_type")
        collection_date = _value(event, "collection_date")
        if source not in SOURCES or not isinstance(collection_date, date):
            continue
        start = week_start_for(collection_date)
        if start in included_weeks:
            counts[start][source] += 1
    return counts
