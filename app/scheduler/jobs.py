import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from threading import Lock, Thread
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.database import SessionLocal
from app.daily.collectors import SOURCE_FIELDS, collect_target_events
from app.events.models import DailySourceSummary
from app.targets.models import Target


logger = logging.getLogger(__name__)
TIMEZONE = "Asia/Kolkata"
COLLECTION_LOCK = Lock()
try:
    STARTUP_CATCH_UP_DAYS = max(0, int(os.getenv("RAVN_DAILY_CATCHUP_DAYS", "3")))
except ValueError:
    logger.warning("Invalid RAVN_DAILY_CATCHUP_DAYS; using the default of 3")
    STARTUP_CATCH_UP_DAYS = 3


def catch_up_missed_days(days: int = STARTUP_CATCH_UP_DAYS, today: date | None = None) -> None:
    """Collect missing completed dates; past-date collection omits snapshots and undated items."""
    today = today or datetime.now(ZoneInfo(TIMEZONE)).date()
    with COLLECTION_LOCK:
        db = SessionLocal()
        try:
            targets = db.query(Target).filter(Target.status == "ready").all()
            for target in targets:
                if target.created_at:
                    created_at = target.created_at
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    created_date = created_at.astimezone(ZoneInfo(TIMEZONE)).date()
                else:
                    created_date = today
                statuses_by_date = defaultdict(list)
                rows = db.query(
                    DailySourceSummary.collection_date,
                    DailySourceSummary.status,
                ).filter(
                    DailySourceSummary.target_id == target.id,
                    DailySourceSummary.collection_date < today,
                ).all()
                for collection_date, status in rows:
                    statuses_by_date[collection_date].append(status)

                missing_dates = []
                offset = 1
                while len(missing_dates) < days:
                    missed_date = today - timedelta(days=offset)
                    if missed_date < created_date:
                        break
                    statuses = statuses_by_date.get(missed_date, [])
                    no_summary = not statuses
                    all_sources_failed = (
                        len(statuses) == len(SOURCE_FIELDS)
                        and all(status == "failed" for status in statuses)
                    )
                    if no_summary or all_sources_failed:
                        missing_dates.append(missed_date)
                    offset += 1
                for missed_date in reversed(missing_dates):
                    report = collect_target_events(db, target, collection_date=missed_date)
                    logger.info("Daily collection catch-up completed: %s", report)
        finally:
            db.close()


def start_catch_up_thread() -> Thread:
    """Start historical catch-up without delaying API startup."""
    thread = Thread(
        target=catch_up_missed_days,
        name="daily-collection-catch-up",
        daemon=True,
    )
    thread.start()
    return thread


def collect_all_ready_targets() -> None:
    with COLLECTION_LOCK:
        db = SessionLocal()
        try:
            collection_date = datetime.now(ZoneInfo(TIMEZONE)).date()
            targets = db.query(Target).filter(Target.status == "ready").all()
            for target in targets:
                report = collect_target_events(db, target, collection_date=collection_date)
                logger.info("Daily collection completed: %s", report)
        finally:
            db.close()


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        collect_all_ready_targets,
        trigger=CronTrigger(hour=23, minute=59, timezone=TIMEZONE),
        id="daily_target_collection",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=12 * 60 * 60,
    )
    return scheduler
