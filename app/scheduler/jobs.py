import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.database import SessionLocal
from app.daily.collectors import collect_target_events
from app.targets.models import Target


logger = logging.getLogger(__name__)
TIMEZONE = "Asia/Kolkata"


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
