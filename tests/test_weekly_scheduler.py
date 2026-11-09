import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.models import User
from app.db.base import Base
from app.events.models import DailySourceSummary
from app.scheduler import jobs
from app.targets.models import Target
from app.weekly.config import WEEKLY_CONFIG
from app.weekly.models import WeeklyRun


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 16, 4, 0, tzinfo=IST)
LAST_CLOSED = date(2026, 11, 9)


class FakeScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, *args, **kwargs):
        self.jobs.append((args, kwargs))


class WeeklySchedulerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        user = User(email="weekly-scheduler@example.invalid", password_hash="x")
        self.session.add(user)
        self.session.flush()
        self.target = Target(
            user_id=user.id,
            name="Scheduler target",
            website_url="https://example.invalid",
            status="ready",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.session.add(self.target)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def add_summary(self, day, status):
        self.session.add(DailySourceSummary(
            target_id=self.target.id,
            collection_date=day,
            source_type="github",
            status=status,
            event_count=0,
            undated_items=0,
            highlight="",
            collected_at=datetime.now(timezone.utc),
        ))
        self.session.commit()

    def test_weekly_cron_is_monday_at_three_ist(self):
        scheduler = jobs.create_scheduler()
        weekly_job = scheduler.get_job("weekly_target_analysis")
        fields = {field.name: str(field) for field in weekly_job.trigger.fields}
        self.assertEqual(fields["day_of_week"], "mon")
        self.assertEqual(fields["hour"], str(WEEKLY_CONFIG.schedule_hour))
        self.assertEqual(fields["minute"], str(WEEKLY_CONFIG.schedule_minute))
        self.assertEqual(str(weekly_job.trigger.timezone), "Asia/Kolkata")

    def test_weekly_startup_catchup_requires_collected_day_and_no_success(self):
        self.add_summary(LAST_CLOSED, "success")
        with patch.object(jobs, "SessionLocal", return_value=self.session), patch.object(
            jobs, "run_weekly_target"
        ) as run:
            jobs.catch_up_weekly_runs(now=NOW)
        run.assert_called_once_with(self.target.id, LAST_CLOSED)

        run_record = WeeklyRun(
            target_id=self.target.id,
            week_start_date=LAST_CLOSED,
            attempt=1,
            status="succeeded",
            retryable=False,
        )
        self.session.add(run_record)
        self.session.commit()
        with patch.object(jobs, "SessionLocal", return_value=self.session), patch.object(
            jobs, "run_weekly_target"
        ) as run:
            jobs.catch_up_weekly_runs(now=NOW)
        run.assert_not_called()

    def test_weekly_startup_catchup_skips_failed_only_or_uncollected_days(self):
        self.add_summary(LAST_CLOSED, "failed")
        self.add_summary(LAST_CLOSED + timedelta(days=1), "skipped")
        with patch.object(jobs, "SessionLocal", return_value=self.session), patch.object(
            jobs, "run_weekly_target"
        ) as run:
            jobs.catch_up_weekly_runs(now=NOW)
        run.assert_not_called()

    def test_weekly_startup_catchup_does_not_retry_terminal_failure(self):
        self.add_summary(LAST_CLOSED, "success")
        self.session.add(WeeklyRun(
            target_id=self.target.id,
            week_start_date=LAST_CLOSED,
            attempt=1,
            status="failed",
            retryable=False,
        ))
        self.session.commit()
        with patch.object(jobs, "SessionLocal", return_value=self.session), patch.object(
            jobs, "run_weekly_target"
        ) as run:
            jobs.catch_up_weekly_runs(now=NOW)
        run.assert_not_called()

    def test_retryable_failure_schedules_backoff_but_nonretryable_does_not(self):
        fake_scheduler = FakeScheduler()
        with (
            patch.object(jobs, "WEEKLY_SCHEDULER", fake_scheduler),
            patch.object(jobs, "SessionLocal", return_value=self.session),
            patch.object(jobs, "run_weekly_analysis", return_value={
                "status": "failed", "retryable": True,
            }),
        ):
            jobs.run_weekly_target(self.target.id, LAST_CLOSED)
        self.assertEqual(len(fake_scheduler.jobs), 1)
        args, kwargs = fake_scheduler.jobs[0]
        self.assertEqual(args[0], jobs.run_weekly_target)
        self.assertEqual(kwargs["args"], [self.target.id, LAST_CLOSED, 1])
        self.assertEqual(kwargs["id"], f"weekly_retry_{self.target.id}_{LAST_CLOSED.isoformat()}_1")
        scheduled_delay = (kwargs["trigger"].run_date - datetime.now(IST)).total_seconds()
        self.assertGreater(scheduled_delay, 9 * 60)
        self.assertLessEqual(scheduled_delay, 10 * 60)

        fake_scheduler.jobs.clear()
        with (
            patch.object(jobs, "WEEKLY_SCHEDULER", fake_scheduler),
            patch.object(jobs, "SessionLocal", return_value=self.session),
            patch.object(jobs, "run_weekly_analysis", return_value={
                "status": "failed", "retryable": False,
            }),
        ):
            jobs.run_weekly_target(self.target.id, LAST_CLOSED)
        self.assertEqual(fake_scheduler.jobs, [])

    def test_retry_stops_after_configured_number(self):
        fake_scheduler = FakeScheduler()
        with (
            patch.object(jobs, "WEEKLY_SCHEDULER", fake_scheduler),
            patch.object(jobs, "SessionLocal", return_value=self.session),
            patch.object(jobs, "run_weekly_analysis", return_value={
                "status": "failed", "retryable": True,
            }),
        ):
            jobs.run_weekly_target(self.target.id, LAST_CLOSED, retry_number=WEEKLY_CONFIG.retry_attempts)
        self.assertEqual(fake_scheduler.jobs, [])


if __name__ == "__main__":
    unittest.main()
