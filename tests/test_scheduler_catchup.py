import unittest
import importlib
import sys
from datetime import date, datetime, time, timedelta, timezone
from threading import Barrier, Lock, Thread
from time import sleep
from types import ModuleType
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.events.models import DailySourceSummary
from app.targets.models import Target
from app.auth.models import User


try:
    from app.scheduler import jobs
except ModuleNotFoundError as exc:
    if exc.name != "apscheduler":
        raise
    # The catch-up tests do not instantiate APScheduler. Stub its two classes
    # so this unit remains offline even when optional runtime deps aren't installed.
    scheduler_modules = {
        "apscheduler": ModuleType("apscheduler"),
        "apscheduler.schedulers": ModuleType("apscheduler.schedulers"),
        "apscheduler.schedulers.background": ModuleType("apscheduler.schedulers.background"),
        "apscheduler.triggers": ModuleType("apscheduler.triggers"),
        "apscheduler.triggers.cron": ModuleType("apscheduler.triggers.cron"),
    }
    scheduler_modules["apscheduler.schedulers.background"].BackgroundScheduler = type("BackgroundScheduler", (), {})
    scheduler_modules["apscheduler.triggers.cron"].CronTrigger = type("CronTrigger", (), {})
    sys.modules.update(scheduler_modules)
    jobs = importlib.import_module("app.scheduler.jobs")


class StartupCatchUpTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.user = User(email="catchup@example.com", password_hash="test")
        self.session.add(self.user)
        self.session.flush()
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        self.target = Target(
            user_id=self.user.id,
            name="Catch up target",
            website_url="https://example.com",
            status="ready",
            created_at=datetime.combine(today - timedelta(days=10), time.min, timezone.utc),
        )
        self.session.add(self.target)
        self.session.commit()
        self.today = today

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def add_summary(self, collection_date):
        self.session.add(DailySourceSummary(
            target_id=self.target.id,
            collection_date=collection_date,
            source_type="github",
            status="success",
            event_count=0,
            undated_items=0,
            highlight="",
            collected_at=datetime.now(timezone.utc),
        ))
        self.session.commit()

    def test_no_gaps_runs_no_catch_up(self):
        self.target.created_at = datetime.combine(
            self.today - timedelta(days=3), time.min, ZoneInfo("Asia/Kolkata")
        )
        self.session.commit()
        for offset in (1, 2, 3):
            self.add_summary(self.today - timedelta(days=offset))
        with patch.object(jobs, "SessionLocal", return_value=self.session), patch.object(jobs, "collect_target_events") as collect:
            jobs.catch_up_missed_days(today=self.today)
        collect.assert_not_called()

    def test_one_gap_is_collected(self):
        # The target was created yesterday, so yesterday is its only completed date.
        self.target.created_at = datetime.combine(
            self.today - timedelta(days=1), time.min, ZoneInfo("Asia/Kolkata")
        )
        self.session.commit()
        with patch.object(jobs, "SessionLocal", return_value=self.session), patch.object(jobs, "collect_target_events") as collect:
            jobs.catch_up_missed_days(today=self.today)
        collect.assert_called_once_with(
            self.session, self.target, collection_date=self.today - timedelta(days=1)
        )

    def test_repeated_startup_does_not_collect_same_gap_twice(self):
        self.target.created_at = datetime.combine(
            self.today - timedelta(days=1), time.min, ZoneInfo("Asia/Kolkata")
        )
        self.session.commit()
        calls = []

        def collect_and_record(db, target, collection_date):
            calls.append(collection_date)
            self.add_summary(collection_date)
            return {"collection_date": collection_date}

        with patch.object(jobs, "SessionLocal", return_value=self.session), patch.object(
            jobs, "collect_target_events", side_effect=collect_and_record
        ):
            jobs.catch_up_missed_days(days=1, today=self.today)
            jobs.catch_up_missed_days(days=1, today=self.today)
        self.assertEqual(calls, [self.today - timedelta(days=1)])

    def test_date_with_all_four_sources_failed_is_retried(self):
        missed_date = self.today - timedelta(days=1)
        for source_type in ("github", "ats", "blog", "web_social"):
            self.session.add(DailySourceSummary(
                target_id=self.target.id,
                collection_date=missed_date,
                source_type=source_type,
                status="failed",
                event_count=0,
                undated_items=0,
                highlight="Collection failed.",
                collected_at=datetime.now(timezone.utc),
            ))
        self.session.commit()
        self.target.created_at = datetime.combine(
            missed_date, time.min, ZoneInfo("Asia/Kolkata")
        )
        self.session.commit()
        with patch.object(jobs, "SessionLocal", return_value=self.session), patch.object(
            jobs, "collect_target_events"
        ) as collect:
            jobs.catch_up_missed_days(days=1, today=self.today)
        collect.assert_called_once_with(self.session, self.target, collection_date=missed_date)

    def test_catch_up_and_scheduled_collection_do_not_overlap(self):
        barrier = Barrier(3)
        active_lock = Lock()
        active = 0
        max_active = 0

        def record_collection(*args, **kwargs):
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            sleep(0.03)
            with active_lock:
                active -= 1
            return {}

        with patch.object(jobs, "SessionLocal", side_effect=self.session_factory), patch.object(
            jobs, "collect_target_events", side_effect=record_collection
        ):
            catchup = Thread(target=lambda: (barrier.wait(), jobs.catch_up_missed_days(days=1, today=self.today)))
            scheduled = Thread(target=lambda: (barrier.wait(), jobs.collect_all_ready_targets()))
            catchup.start()
            scheduled.start()
            barrier.wait()
            catchup.join()
            scheduled.join()

        self.assertEqual(max_active, 1)

    def test_app_starts_scheduler_before_background_catch_up(self):
        from app import main

        calls = []

        class SchedulerStub:
            def start(self):
                calls.append("scheduler_started")

        with (
            patch.object(main, "create_scheduler", return_value=SchedulerStub()),
            patch.object(main, "start_catch_up_thread", side_effect=lambda: calls.append("catchup_started")),
        ):
            main.start_daily_scheduler()

        self.assertEqual(calls, ["scheduler_started", "catchup_started"])


if __name__ == "__main__":
    unittest.main()
