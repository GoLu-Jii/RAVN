import unittest
from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.targets.models import Target
from app.weekly.models import WeeklyResult, WeeklyRun, WeeklySignal
from app.weekly.repository import (
    complete_run,
    create_run,
    fail_run,
    get_owned_target,
    get_result_for_owner,
    list_results_for_owner,
    list_runs_for_owner,
)


WEEK_START = date(2026, 11, 2)


def weekly_input():
    return {
        "metadata": {
            "history_weeks_available": 2,
            "prompt_version": "weekly-v1",
            "compaction_level": "none",
            "estimated_tokens": 1234,
        },
        "current_week_events": [],
    }


def llm_result(summary="First result"):
    return {
        "output": {"summary": summary, "data_limitations": ["ATS partial"], "signals": []},
        "raw_output": '{"summary":"result"}',
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "prompt_version": "weekly-v1",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def signal(title="Hiring for infrastructure"):
    return {
        "title": title,
        "description": "Hiring and code activity point to platform investment.",
        "connection_note": "Role and commit timing overlap.",
        "sources": ["github", "ats"],
        "signal_types": ["spike"],
        "score": 2.5,
        "configured_sources": 4,
        "confirmed": 2,
        "uncertain": 1,
        "source_states": {"github": {"state": "supported", "score": 1.0}},
        "cited_event_ids": [10, 11],
        "uses_first_observed": False,
    }


class WeeklyRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.owner = User(email="weekly-owner@example.invalid", password_hash="x")
        self.other = User(email="weekly-other@example.invalid", password_hash="x")
        self.session.add_all([self.owner, self.other])
        self.session.flush()
        self.target = self.make_target(self.owner.id, "Owner target")
        self.other_target = self.make_target(self.other.id, "Other target")
        self.session.add_all([self.target, self.other_target])
        self.session.commit()

    def make_target(self, owner_id, name):
        return Target(
            user_id=owner_id,
            name=name,
            website_url="https://example.invalid",
            status="ready",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def succeed(self, target_id, attempt, summary, signals):
        run = create_run(self.session, target_id=target_id, week_start=WEEK_START, attempt=attempt)
        result = complete_run(
            self.session,
            run,
            weekly_input=weekly_input(),
            llm_result=llm_result(summary),
            source_states={"github": {"coverage": "complete"}},
            exact_counts=[{"week_start": "2026-11-02", "sources": {"github": 2}}],
            signals=signals,
        )
        return run, result

    def test_result_is_unique_and_rerun_replaces_signals_but_accumulates_runs(self):
        first_run, first_result = self.succeed(self.target.id, 1, "First result", [signal("Old")])
        second_run, second_result = self.succeed(self.target.id, 2, "Updated result", [signal("New")])
        self.assertEqual(first_result.id, second_result.id)
        self.assertEqual(second_result.summary, "Updated result")
        self.assertEqual(second_result.latest_successful_run_id, second_run.id)
        self.assertEqual(self.session.query(WeeklyRun).filter_by(target_id=self.target.id).count(), 2)
        signals = self.session.query(WeeklySignal).filter_by(result_id=second_result.id).all()
        self.assertEqual([item.title for item in signals], ["New"])
        self.assertEqual(self.session.query(WeeklyResult).filter_by(target_id=self.target.id).count(), 1)
        self.assertEqual(first_run.status, "succeeded")

    def test_failed_run_does_not_overwrite_latest_successful_result(self):
        successful_run, result = self.succeed(self.target.id, 1, "Kept result", [signal()])
        failed = create_run(self.session, target_id=self.target.id, week_start=WEEK_START, attempt=2)
        fail_run(self.session, failed, error="rate limit", retryable=True, weekly_input=weekly_input())
        current = self.session.query(WeeklyResult).filter_by(target_id=self.target.id).one()
        self.assertEqual(current.id, result.id)
        self.assertEqual(current.summary, "Kept result")
        self.assertEqual(current.latest_successful_run_id, successful_run.id)
        self.assertEqual(self.session.query(WeeklySignal).filter_by(result_id=result.id).count(), 1)
        self.assertEqual(failed.status, "failed")
        self.assertTrue(failed.retryable)

    def test_success_transaction_rolls_back_all_replacement_on_bad_signal(self):
        _, result = self.succeed(self.target.id, 1, "Original", [signal("Original signal")])
        failed_candidate = create_run(self.session, target_id=self.target.id, week_start=WEEK_START, attempt=2)
        with self.assertRaises(KeyError):
            complete_run(
                self.session,
                failed_candidate,
                weekly_input=weekly_input(),
                llm_result=llm_result("Should roll back"),
                source_states={},
                exact_counts=[],
                signals=[{"title": "missing required fields"}],
            )
        self.session.refresh(failed_candidate)
        current = self.session.query(WeeklyResult).filter_by(target_id=self.target.id).one()
        self.assertEqual(current.id, result.id)
        self.assertEqual(current.summary, "Original")
        self.assertEqual(
            [row.title for row in self.session.query(WeeklySignal).filter_by(result_id=result.id)],
            ["Original signal"],
        )
        self.assertEqual(failed_candidate.status, "running")

    def test_unrelated_target_rows_and_run_history_remain_untouched(self):
        self.succeed(self.target.id, 1, "Owner current", [signal()])
        other_run, other_result = self.succeed(self.other_target.id, 1, "Other current", [])
        self.succeed(self.target.id, 2, "Owner rerun", [])
        self.assertEqual(self.session.query(WeeklyResult).filter_by(target_id=self.other_target.id).one().id, other_result.id)
        self.assertEqual(self.session.query(WeeklyRun).filter_by(target_id=self.other_target.id).one().id, other_run.id)
        self.assertEqual(self.session.query(WeeklyRun).filter_by(target_id=self.target.id).count(), 2)

    def test_unique_attempt_and_unique_result_constraints(self):
        create_run(self.session, target_id=self.target.id, week_start=WEEK_START, attempt=1)
        self.session.add(WeeklyRun(
            target_id=self.target.id,
            week_start_date=WEEK_START,
            attempt=1,
            status="running",
            retryable=False,
        ))
        with self.assertRaises(IntegrityError):
            self.session.commit()
        self.session.rollback()

        self.session.add(WeeklyResult(
            target_id=self.target.id,
            week_start_date=WEEK_START,
            source_states={},
            exact_counts=[],
            status="ok",
            summary="first",
            data_limitations=[],
            limited_history=False,
        ))
        self.session.commit()
        self.session.add(WeeklyResult(
            target_id=self.target.id,
            week_start_date=WEEK_START,
            source_states={},
            exact_counts=[],
            status="ok",
            summary="duplicate",
            data_limitations=[],
            limited_history=False,
        ))
        with self.assertRaises(IntegrityError):
            self.session.commit()
        self.session.rollback()

    def test_owner_scoped_lookup_helpers(self):
        _, result = self.succeed(self.target.id, 1, "Owned", [])
        self.assertEqual(get_owned_target(self.session, self.target.id, self.owner.id).id, self.target.id)
        self.assertIsNone(get_owned_target(self.session, self.target.id, self.other.id))
        self.assertEqual(get_result_for_owner(self.session, self.target.id, self.owner.id, WEEK_START).id, result.id)
        self.assertIsNone(get_result_for_owner(self.session, self.target.id, self.other.id, WEEK_START))
        self.assertEqual(list_results_for_owner(self.session, self.target.id, self.owner.id)[0].id, result.id)
        self.assertIsNone(list_results_for_owner(self.session, self.target.id, self.other.id))
        self.assertEqual(len(list_runs_for_owner(self.session, self.target.id, self.owner.id, WEEK_START)), 1)
        self.assertIsNone(list_runs_for_owner(self.session, self.target.id, self.other.id, WEEK_START))


if __name__ == "__main__":
    unittest.main()
