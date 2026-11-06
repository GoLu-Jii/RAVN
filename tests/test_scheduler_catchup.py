import unittest
import importlib
import sys
from datetime import date, datetime, time, timedelta, timezone
from types import ModuleType
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
    with patch.dict(sys.modules, scheduler_modules):
        jobs = importlib.import_module("app.scheduler.jobs")


class StartupCatchUpTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
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


if __name__ == "__main__":
    unittest.main()
