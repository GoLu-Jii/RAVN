import time as time_module
from datetime import date, datetime, time, timezone
from email.utils import parsedate_to_datetime


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _parse_date(value) -> datetime | None:
    """Parse source dates into UTC-aware datetimes for storage.

    Aware datetimes are converted to Asia/Kolkata before deriving the collection
    calendar date. Naive datetimes are treated as UTC. Date-only values retain
    their calendar date. Unparseable or empty values return None.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, date):
        return datetime.combine(value, time.min, timezone.utc)
    if isinstance(value, time_module.struct_time):
        try:
            return datetime(*value[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None
    try:
        if isinstance(value, (int, float)):
            timestamp = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(timestamp, timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return _as_utc(parsed)
    except (TypeError, ValueError, OverflowError):
        try:
            return _as_utc(parsedate_to_datetime(str(value)))
        except (TypeError, ValueError, OverflowError):
            return None
