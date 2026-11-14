"""Week windows use Asia/Kolkata calendar dates because events are grouped by collection_date."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


WEEK_TIMEZONE = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class WeekWindow:
    start_date: date
    end_date: date


def _local_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        # Naive values from callers are product-local; aware instants are
        # converted before taking the date so UTC midnight is not misbucketed.
        localized = value.replace(tzinfo=WEEK_TIMEZONE) if value.tzinfo is None else value.astimezone(WEEK_TIMEZONE)
        return localized.date()
    return value


def week_start_for(value: date | datetime) -> date:
    """Return the Monday of the Asia/Kolkata week containing a date or instant."""
    local_day = _local_date(value)
    return local_day - timedelta(days=local_day.weekday())


def week_window(value: date | datetime) -> WeekWindow:
    """Return the inclusive Monday-to-Sunday collection_date window."""
    start = week_start_for(value)
    return WeekWindow(start_date=start, end_date=start + timedelta(days=6))


def require_monday(week_start: date) -> date:
    if week_start.weekday() != 0:
        raise ValueError("week_start must be a Monday.")
    return week_start


def previous_week_starts(week_start: date, count: int = 12) -> list[date]:
    """Return prior Monday dates oldest-first, excluding the supplied week."""
    require_monday(week_start)
    if count < 0:
        raise ValueError("count cannot be negative.")
    return [week_start - timedelta(weeks=offset) for offset in range(count, 0, -1)]


def last_closed_week(now: datetime | None = None) -> date:
    """Return the Monday of the last fully closed Monday-to-Sunday week."""
    instant = now or datetime.now(WEEK_TIMEZONE)
    return week_start_for(instant) - timedelta(days=7)
