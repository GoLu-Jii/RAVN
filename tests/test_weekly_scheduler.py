import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.models import User
from app.db.base import Base
from app.scheduler import jobs
from app.targets.models import Target
from app.weekly.config import WEEKLY_CONFIG
from app.weekly.models import SignalDetectionRun, WeeklyLeadRun


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

    def add_run(self, *, status, retryable=False):
        self.session.add(WeeklyLeadRun(
            target_id=self.target.id,
            week_start_date=LAST_CLOSED,
            attempt=1,
            status=status,
            is_current=status in {"succeeded", "no_events"},
            retryable=retryable,
            input_event_ids=[],
        ))
        self.session.commit()

    def test_weekly_cron_is_monday_at_two_ist(self):
        scheduler = jobs.create_scheduler()
        weekly_job = scheduler.get_job("weekly_target_analysis")
        fields = {field.name: str(field) for field in weekly_job.trigger.fields}
        self.assertEqual(fields["day_of_week"], "mon")
        self.assertEqual(fields["hour"], "2")
        self.assertEqual(fields["minute"], "0")
        self.assertEqual(str(weekly_job.trigger.timezone), "Asia/Kolkata")

    def test_signal_cron_is_monday_at_four_ist(self):
        scheduler = jobs.create_scheduler()
        signal_job = scheduler.get_job("weekly_signal_detection")
        fields = {field.name: str(field) for field in signal_job.trigger.fields}
        self.assertEqual(fields["day_of_week"], "mon")
        self.assertEqual(fields["hour"], "4")
        self.assertEqual(fields["minute"], "0")
        self.assertEqual(str(signal_job.trigger.timezone), "Asia/Kolkata")

    def test_signal_waits_for_weekly_run_with_three_total_checks(self):
        fake_scheduler = FakeScheduler()
        with (
            patch.object(jobs, "WEEKLY_SCHEDULER", fake_scheduler),
            patch.object(jobs, "SessionLocal", return_value=self.session),
            patch.object(jobs, "run_signal_detection") as run_signal,
        ):
            jobs.run_signal_for_ready_targets(LAST_CLOSED, check_number=1)
        run_signal.assert_not_called()
        self.assertEqual(len(fake_scheduler.jobs), 1)
        self.assertEqual(fake_scheduler.jobs[0][1]["args"], [LAST_CLOSED, 2])

        fake_scheduler.jobs.clear()
        with (
            patch.object(jobs, "WEEKLY_SCHEDULER", fake_scheduler),
            patch.object(jobs, "SessionLocal", return_value=self.session),
            patch.object(jobs, "run_signal_detection") as run_signal,
        ):
            jobs.run_signal_for_ready_targets(LAST_CLOSED, check_number=3)
        run_signal.assert_not_called()
        self.assertEqual(fake_scheduler.jobs, [])

    def test_signal_runs_only_after_weekly_success(self):
        self.add_run(status="succeeded")
        with (
            patch.object(jobs, "SessionLocal", return_value=self.session),
            patch.object(jobs, "run_signal_detection", return_value={"status": "succeeded"}) as run,
        ):
            jobs.run_signal_for_ready_targets(LAST_CLOSED)
        run.assert_called_once()

    def test_signal_gate_allows_exactly_one_retry_after_first_weekly_failure(self):
        self.add_run(status="failed", retryable=True)
        with (
            patch.object(jobs, "SessionLocal", return_value=self.session),
            patch.object(jobs, "run_weekly_target", return_value={"status": "succeeded"}) as retry,
            patch.object(jobs, "run_signal_detection", return_value={"status": "succeeded"}) as run,
        ):
            jobs.run_signal_for_ready_targets(LAST_CLOSED)
        retry.assert_called_once_with(
            self.target.id,
            LAST_CLOSED,
            retry_number=WEEKLY_CONFIG.retry_attempts,
        )
        run.assert_called_once()

        self.session.query(WeeklyLeadRun).delete()
        self.session.commit()
        self.add_run(status="failed", retryable=True)
        latest = self.session.query(WeeklyLeadRun).one()
        latest.attempt = 2
        self.session.commit()
        with (
            patch.object(jobs, "SessionLocal", return_value=self.session),
            patch.object(jobs, "run_weekly_target") as retry,
            patch.object(jobs, "run_signal_detection") as run,
        ):
            jobs.run_signal_for_ready_targets(LAST_CLOSED)
        retry.assert_not_called()
        run.assert_not_called()

    def test_weekly_startup_catchup_runs_without_daily_event_rows(self):
        with patch.object(jobs, "SessionLocal", return_value=self.session), patch.object(
            jobs, "run_weekly_target"
        ) as run:
            jobs.catch_up_weekly_runs(now=NOW)
        run.assert_called_once_with(self.target.id, LAST_CLOSED)

        self.add_run(status="no_events")
        with patch.object(jobs, "SessionLocal", return_value=self.session), patch.object(
            jobs, "run_weekly_target"
        ) as run:
            jobs.catch_up_weekly_runs(now=NOW)
        run.assert_not_called()

    def test_weekly_startup_catchup_does_not_repeat_terminal_failure(self):
        self.add_run(status="failed", retryable=False)
        with patch.object(jobs, "SessionLocal", return_value=self.session), patch.object(
            jobs, "run_weekly_target"
        ) as run:
            jobs.catch_up_weekly_runs(now=NOW)
        run.assert_not_called()

    def test_retryable_failure_schedules_one_retry(self):
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

    def test_retry_stops_after_configured_retry(self):
        fake_scheduler = FakeScheduler()
        with (
            patch.object(jobs, "WEEKLY_SCHEDULER", fake_scheduler),
            patch.object(jobs, "SessionLocal", return_value=self.session),
            patch.object(jobs, "run_weekly_analysis", return_value={
                "status": "failed", "retryable": True,
            }),
        ):
            jobs.run_weekly_target(
                self.target.id,
                LAST_CLOSED,
                retry_number=WEEKLY_CONFIG.retry_attempts,
            )
        self.assertEqual(fake_scheduler.jobs, [])


if __name__ == "__main__":
    unittest.main()
