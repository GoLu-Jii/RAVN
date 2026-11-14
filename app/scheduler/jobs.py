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
from app.weekly.models import SignalDetectionRun, WeeklyLeadRun
from app.weekly.signal_detector import (
    SignalDetectorBusy,
    run_signal_detection,
)
from app.weekly.windows import last_closed_week, require_monday


logger = logging.getLogger(__name__)
TIMEZONE = "Asia/Kolkata"
COLLECTION_LOCK = Lock()
WEEKLY_SCHEDULER = None
WEEKLY_RETRY_DELAYS_MINUTES = (10,)
SIGNAL_CHECK_INTERVAL_MINUTES = 30
SIGNAL_MAX_CHECKS = 3
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
    """Run the latest closed week unless it has a success or terminal failure."""
    instant = now or datetime.now(ZoneInfo(TIMEZONE))
    week_start = last_closed_week(instant)
    db = SessionLocal()
    try:
        targets = db.query(Target).filter(Target.status == "ready").all()
        eligible_target_ids = []
        for target in targets:
            latest_run = db.query(WeeklyLeadRun).filter_by(
                target_id=target.id,
                week_start_date=week_start,
            ).order_by(WeeklyLeadRun.attempt.desc()).first()
            if latest_run is not None and (
                latest_run.status in {"succeeded", "no_events"}
                or (latest_run.status == "failed" and not latest_run.retryable)
            ):
                continue
            eligible_target_ids.append(target.id)
    finally:
        db.close()
    for target_id in eligible_target_ids:
        run_weekly_target(target_id, week_start)


def _schedule_signal_check(week_start: date, check_number: int) -> None:
    if WEEKLY_SCHEDULER is None:
        logger.warning("Cannot schedule Signal Detector check: scheduler is not available")
        return
    run_at = datetime.now(ZoneInfo(TIMEZONE)) + timedelta(
        minutes=SIGNAL_CHECK_INTERVAL_MINUTES
    )
    WEEKLY_SCHEDULER.add_job(
        run_signal_for_ready_targets,
        trigger=DateTrigger(run_date=run_at, timezone=TIMEZONE),
        args=[week_start, check_number + 1],
        id=f"signal_check_{week_start.isoformat()}_{check_number + 1}",
        replace_existing=True,
        max_instances=1,
    )


def run_signal_for_ready_targets(
    week_start: date | None = None,
    check_number: int = 1,
) -> None:
    """Run signals only after weekly lead success; wait for at most three checks."""
    selected_week = week_start or last_closed_week(datetime.now(ZoneInfo(TIMEZONE)))
    if check_number < 1 or check_number > SIGNAL_MAX_CHECKS:
        logger.error("Refusing Signal Detector check number %s", check_number)
        return
    db = SessionLocal()
    try:
        target_ids = [
            target_id for (target_id,) in db.query(Target.id).filter(
                Target.status == "ready"
            ).all()
        ]
    finally:
        db.close()

    pending = False
    for target_id in target_ids:
        db = SessionLocal()
        try:
            signal_run = db.query(SignalDetectionRun).filter_by(
                target_id=target_id,
                week_start_date=selected_week,
            ).order_by(SignalDetectionRun.attempt.desc()).first()
            if signal_run is not None:
                continue
            weekly_run = db.query(WeeklyLeadRun).filter_by(
                target_id=target_id,
                week_start_date=selected_week,
            ).order_by(WeeklyLeadRun.attempt.desc()).first()
            if weekly_run is None or weekly_run.status == "running":
                pending = True
                continue
            if weekly_run.status == "failed":
                if weekly_run.attempt != 1 or not weekly_run.retryable:
                    logger.warning(
                        "Skipping Signal Detector for target %s week %s after weekly failure",
                        target_id,
                        selected_week,
                    )
                    continue
                db.close()
                retry_result = run_weekly_target(
                    target_id,
                    selected_week,
                    retry_number=WEEKLY_CONFIG.retry_attempts,
                )
                if retry_result is None:
                    pending = True
                    continue
                if retry_result.get("status") == "failed":
                    logger.warning(
                        "Weekly retry failed for target %s week %s; skipping signals",
                        target_id,
                        selected_week,
                    )
                    continue
                if retry_result.get("status") not in {"succeeded", "no_events"}:
                    pending = True
                    continue
            elif weekly_run.status not in {"succeeded", "no_events"}:
                logger.warning(
                    "Skipping Signal Detector for target %s week %s with weekly status %s",
                    target_id,
                    selected_week,
                    weekly_run.status,
                )
                continue
            elif not weekly_run.is_current:
                logger.warning(
                    "Skipping Signal Detector for target %s week %s because the successful weekly run is not current",
                    target_id,
                    selected_week,
                )
                continue
        finally:
            if db.is_active:
                db.close()

        db = SessionLocal()
        try:
            result = run_signal_detection(
                db,
                target_id=target_id,
                week_start=selected_week,
            )
            logger.info("Signal Detector completed: %s", result)
        except SignalDetectorBusy:
            pending = True
            logger.warning(
                "Signal Detector already running for target %s week %s",
                target_id,
                selected_week,
            )
        except Exception:
            logger.exception(
                "Signal Detector failed for target %s week %s",
                target_id,
                selected_week,
            )
        finally:
            db.close()

    if pending and check_number < SIGNAL_MAX_CHECKS:
        _schedule_signal_check(selected_week, check_number)
    elif pending:
        logger.warning(
            "Skipping Signal Detector for week %s after %s checks",
            selected_week,
            SIGNAL_MAX_CHECKS,
        )


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
    scheduler.add_job(
        run_signal_for_ready_targets,
        trigger=CronTrigger(
            day_of_week="mon",
            hour=WEEKLY_CONFIG.signal_schedule_hour,
            minute=0,
            timezone=TIMEZONE,
        ),
        id="weekly_signal_detection",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=12 * 60 * 60,
    )
    WEEKLY_SCHEDULER = scheduler
    return scheduler
