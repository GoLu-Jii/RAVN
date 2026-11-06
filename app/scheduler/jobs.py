import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.database import SessionLocal
from app.daily.collectors import collect_target_events
from app.events.models import DailySourceSummary
from app.targets.models import Target


logger = logging.getLogger(__name__)
TIMEZONE = "Asia/Kolkata"
try:
    STARTUP_CATCH_UP_DAYS = max(0, int(os.getenv("RAVN_DAILY_CATCHUP_DAYS", "3")))
except ValueError:
    logger.warning("Invalid RAVN_DAILY_CATCHUP_DAYS; using the default of 3")
    STARTUP_CATCH_UP_DAYS = 3


def catch_up_missed_days(days: int = STARTUP_CATCH_UP_DAYS, today: date | None = None) -> None:
    """Collect missing completed dates; past-date collection omits snapshots and undated items."""
    today = today or datetime.now(ZoneInfo(TIMEZONE)).date()
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
            summary_dates = {
                row[0]
                for row in db.query(DailySourceSummary.collection_date)
                .filter(
                    DailySourceSummary.target_id == target.id,
                    DailySourceSummary.collection_date < today,
                )
                .distinct()
                .all()
            }
            missing_dates = []
            offset = 1
            while len(missing_dates) < days:
                missed_date = today - timedelta(days=offset)
                if missed_date < created_date:
                    break
                if missed_date not in summary_dates:
                    missing_dates.append(missed_date)
                offset += 1
            for missed_date in reversed(missing_dates):
                report = collect_target_events(db, target, collection_date=missed_date)
                logger.info("Daily collection catch-up completed: %s", report)
    finally:
        db.close()


def collect_all_ready_targets() -> None:
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
