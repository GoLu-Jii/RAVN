"""Pure calculation of source activity and collection coverage for one week."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from app.weekly.windows import require_monday


SOURCES = ("github", "ats", "blog", "web_social")
SOURCE_URL_FIELDS = {
    "github": "github_url",
    "ats": "ats_url",
    "blog": "blog_url",
    "web_social": "web_social_url",
}
PRODUCT_TIMEZONE = ZoneInfo("Asia/Kolkata")
COLLECTED_STATUSES = {"success", "baseline_only"}


@dataclass(frozen=True)
class SourceCoverage:
    source_type: str
    configured: bool
    activity: str
    coverage: str
    event_count: int
    expected_days: int
    collected_days: int
    missing_dates: tuple[date, ...]
    candidates_total: int
    known_items: int
    failed_items: int
    cap_skipped_items: int
    unverified_share: float
    reasons: tuple[str, ...]


def _value(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _collection_day(value) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(PRODUCT_TIMEZONE).date() if value.tzinfo else value.date()
    return value if isinstance(value, date) else None


def _target_creation_day(target) -> date | None:
    created_at = _value(target, "created_at")
    if created_at is None:
        return None
    if isinstance(created_at, date) and not isinstance(created_at, datetime):
        return created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(PRODUCT_TIMEZONE).date()


def source_coverage(
    target,
    events: Iterable,
    summaries: Iterable,
    week_start: date,
    *,
    today: date | None = None,
    unverified_threshold: float = 0.20,
) -> dict[str, SourceCoverage]:
    """Compute configured/activity/coverage states without querying the database."""
    require_monday(week_start)
    if not 0 <= unverified_threshold <= 1:
        raise ValueError("unverified_threshold must be between 0 and 1.")
    events = list(events)
    summaries = list(summaries)

    week_end = week_start + timedelta(days=6)
    local_today = today or datetime.now(PRODUCT_TIMEZONE).date()
    yesterday = local_today - timedelta(days=1)
    created_day = _target_creation_day(target)
    expected_start = max(week_start, created_day) if created_day else week_start
    expected_end = min(week_end, yesterday)
    expected_dates = (
        tuple(expected_start + timedelta(days=n) for n in range((expected_end - expected_start).days + 1))
        if expected_start <= expected_end else ()
    )
    expected_set = set(expected_dates)
    relevant_summaries = [
        row for row in summaries
        if _collection_day(_value(row, "collection_date")) in expected_set
    ]
    summary_by_day = {
        (_collection_day(_value(row, "collection_date")), _value(row, "source_type")): row
        for row in relevant_summaries
    }

    result = {}
    for source in SOURCES:
        configured = bool(_value(target, SOURCE_URL_FIELDS[source]))
        active_events = [
            event for event in events
            if _value(event, "source_type") == source
            and not bool(_value(event, "is_baseline", False))
            and week_start <= (_collection_day(_value(event, "collection_date")) or date.min) <= week_end
        ]
        event_count = len(active_events)
        activity = "not_configured" if not configured else "active" if event_count else "quiet"
        reasons: list[str] = []

        collected_dates = {
            day for day in expected_dates
            if (row := summary_by_day.get((day, source))) is not None
            and _value(row, "status") in COLLECTED_STATUSES
        }
        missing_dates = tuple(day for day in expected_dates if day not in collected_dates)
        relevant_source_summaries = [
            row for row in relevant_summaries if _value(row, "source_type") == source
        ]
        candidates_total = sum(int(_value(row, "candidates_total", 0) or 0) for row in relevant_source_summaries)
        known_items = sum(int(_value(row, "known_items", 0) or 0) for row in relevant_source_summaries)
        failed_items = sum(int(_value(row, "failed_items", 0) or 0) for row in relevant_source_summaries)
        cap_skipped_items = sum(int(_value(row, "cap_skipped_items", 0) or 0) for row in relevant_source_summaries)
        denominator = max(candidates_total - known_items, 0)
        unverified_share = (
            (failed_items + cap_skipped_items) / denominator
            if source in {"ats", "blog"} and denominator else 0.0
        )

        if not configured:
            coverage = "not_configured"
            reasons.append("source_not_configured")
        elif not expected_dates:
            coverage = "complete"
            reasons.append("no_expected_days_after_onboarding_cutoff")
        elif not collected_dates:
            coverage = "unavailable"
            reasons.append("no_expected_day_was_collected")
        else:
            coverage = "complete"
            if missing_dates:
                coverage = "partial"
                reasons.append("one_or_more_expected_days_missing")
            if source in {"ats", "blog"} and unverified_share >= unverified_threshold:
                coverage = "partial"
                reasons.append("unverified_items_at_or_above_threshold")

        result[source] = SourceCoverage(
            source_type=source,
            configured=configured,
            activity=activity,
            coverage=coverage,
            event_count=event_count,
            expected_days=len(expected_dates),
            collected_days=len(collected_dates),
            missing_dates=missing_dates,
            candidates_total=candidates_total,
            known_items=known_items,
            failed_items=failed_items,
            cap_skipped_items=cap_skipped_items,
            unverified_share=unverified_share,
            reasons=tuple(reasons),
        )
    return result
