import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from threading import Lock, Thread
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.db.database import SessionLocal
from app.daily.collectors import SOURCE_FIELDS, collect_target_events
from app.embeddings.service import embed_missing_events
from app.events.models import DailySourceSummary
from app.targets.models import Target
from app.weekly.config import WEEKLY_CONFIG
from app.weekly.detector import WeeklyRunBusy, run_weekly_analysis
from app.weekly.models import WeeklyResult, WeeklyRun
from app.weekly.windows import last_closed_week, require_monday


logger = logging.getLogger(__name__)
TIMEZONE = "Asia/Kolkata"
COLLECTION_LOCK = Lock()
WEEKLY_SCHEDULER = None
WEEKLY_RETRY_DELAYS_MINUTES = (10, 30, 60, 120, 240, 240)
try:
    STARTUP_CATCH_UP_DAYS = max(0, int(os.getenv("RAVN_DAILY_CATCHUP_DAYS", "3")))
except ValueError:
    logger.warning("Invalid RAVN_DAILY_CATCHUP_DAYS; using the default of 3")
    STARTUP_CATCH_UP_DAYS = 3


def _embed_target_events(db, target_id: int) -> None:
    """Embedding failures are isolated from daily collection and logged."""
    try:
        report = embed_missing_events(db, target_id=target_id)
        logger.info("Event embedding completed for target %s: %s", target_id, report)
    except Exception:
        logger.exception("Event embedding failed for target %s", target_id)


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
                _embed_target_events(db, target.id)
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
                _embed_target_events(db, target.id)
        finally:
            db.close()


def _schedule_weekly_retry(target_id: int, week_start: date, retry_number: int) -> None:
    if WEEKLY_SCHEDULER is None:
        logger.warning("Cannot schedule weekly retry: scheduler is not available")
        return
    delay = WEEKLY_RETRY_DELAYS_MINUTES[min(retry_number, len(WEEKLY_RETRY_DELAYS_MINUTES) - 1)]
    retry_at = datetime.now(ZoneInfo(TIMEZONE)) + timedelta(minutes=delay)
    WEEKLY_SCHEDULER.add_job(
        run_weekly_target,
        trigger=DateTrigger(run_date=retry_at, timezone=TIMEZONE),
        args=[target_id, week_start, retry_number + 1],
        id=f"weekly_retry_{target_id}_{week_start.isoformat()}_{retry_number + 1}",
        replace_existing=True,
        max_instances=1,
    )
    logger.info(
        "Scheduled weekly retry %s for target %s week %s in %s minutes",
        retry_number + 1,
        target_id,
        week_start,
        delay,
    )


def run_weekly_target(target_id: int, week_start: date, retry_number: int = 0) -> dict | None:
    try:
        require_monday(week_start)
    except ValueError:
        logger.exception("Refusing weekly run with a non-Monday week start: %s", week_start)
        return None
    db = SessionLocal()
    try:
        result = run_weekly_analysis(db, target_id=target_id, week_start=week_start)
        logger.info("Weekly analysis completed: %s", result)
        if (
            result.get("status") == "failed"
            and result.get("retryable")
            and retry_number < WEEKLY_CONFIG.retry_attempts
        ):
            _schedule_weekly_retry(target_id, week_start, retry_number)
        return result
    except WeeklyRunBusy:
        logger.warning("Weekly analysis already running; skipped target %s week %s", target_id, week_start)
        return None
    except Exception:
        logger.exception("Weekly analysis job crashed for target %s week %s", target_id, week_start)
        return None
    finally:
        db.close()


def run_weekly_for_ready_targets() -> None:
    week_start = last_closed_week(datetime.now(ZoneInfo(TIMEZONE)))
    db = SessionLocal()
    try:
        target_ids = [
            target_id for (target_id,) in db.query(Target.id).filter(Target.status == "ready").all()
        ]
    finally:
        db.close()
    for target_id in target_ids:
        run_weekly_target(target_id, week_start)


def catch_up_weekly_runs(now: datetime | None = None) -> None:
    """Rerun the latest closed week when it has collected data but no success."""
    instant = now or datetime.now(ZoneInfo(TIMEZONE))
    week_start = last_closed_week(instant)
    week_end = week_start + timedelta(days=6)
    db = SessionLocal()
    try:
        targets = db.query(Target).filter(Target.status == "ready").all()
        eligible_target_ids = []
        for target in targets:
            latest_run = db.query(WeeklyRun).filter_by(
                target_id=target.id,
                week_start_date=week_start,
            ).order_by(WeeklyRun.attempt.desc()).first()
            if latest_run is not None and (
                latest_run.status == "succeeded"
                or (latest_run.status == "failed" and not latest_run.retryable)
            ):
                continue
            collected = db.query(DailySourceSummary.id).filter(
                DailySourceSummary.target_id == target.id,
                DailySourceSummary.collection_date >= week_start,
                DailySourceSummary.collection_date <= week_end,
                DailySourceSummary.status.in_(("success", "baseline_only")),
            ).first()
            if collected:
                eligible_target_ids.append(target.id)
    finally:
        db.close()
    for target_id in eligible_target_ids:
        run_weekly_target(target_id, week_start)


def start_weekly_catch_up_thread(after_thread: Thread | None = None) -> Thread:
    def catch_up_after_daily() -> None:
        if after_thread is not None:
            after_thread.join()
        catch_up_weekly_runs()

    thread = Thread(
        target=catch_up_after_daily,
        name="weekly-analysis-catch-up",
        daemon=True,
    )
    thread.start()
    return thread


def create_scheduler() -> BackgroundScheduler:
    global WEEKLY_SCHEDULER
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
    scheduler.add_job(
        run_weekly_for_ready_targets,
        trigger=CronTrigger(
            day_of_week="mon",
            hour=WEEKLY_CONFIG.schedule_hour,
            minute=WEEKLY_CONFIG.schedule_minute,
            timezone=TIMEZONE,
        ),
        id="weekly_target_analysis",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=12 * 60 * 60,
    )
    WEEKLY_SCHEDULER = scheduler
    return scheduler
