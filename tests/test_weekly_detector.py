import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.events.models import Event
from app.targets.models import Target
from app.weekly.config import WeeklyConfig
from app.weekly.detector import WeeklyRunBusy, run_weekly_analysis
from app.weekly.llm_stage import WeeklyLLMError
from app.weekly.models import WeeklyResult, WeeklyRun, WeeklySignal


WEEK_START = date(2026, 11, 2)


def input_payload(current=True):
    return {
        "metadata": {
            "history_weeks_available": 0,
            "prompt_version": "weekly-v1",
            "estimated_tokens": 800,
            "compaction_level": "none",
        },
        "sources": [
            {"source": "github", "configured": True, "coverage": "complete", "activity": "active"},
            {"source": "ats", "configured": True, "coverage": "partial", "activity": "quiet"},
            {"source": "blog", "configured": False, "coverage": "not_configured", "activity": "not_configured"},
            {"source": "web_social", "configured": False, "coverage": "not_configured", "activity": "not_configured"},
        ],
        "weekly_counts": [{"week_start": WEEK_START.isoformat(), "sources": {"github": 1, "ats": 0, "blog": 0, "web_social": 0}}],
        "current_week_events": [{"event_id": 1}] if current else [],
        "retrieved_history": {"examples": []},
        "baseline_context": {"available": False},
    }


def llm_output(signals=None, summary="LLM summary"):
    return {
        "output": {
            "summary": summary,
            "data_limitations": [],
            "signals": signals or [],
        },
        "raw_output": "{\"summary\":\"LLM summary\"}",
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "prompt_version": "weekly-v1",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


class WeeklyDetectorTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        user = User(email="detector@example.invalid", password_hash="x")
        self.session.add(user)
        self.session.flush()
        self.target = Target(
            user_id=user.id,
            name="Detector target",
            website_url="https://example.invalid",
            github_url="https://github.com/org/repo",
            status="ready",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.session.add(self.target)
        self.session.flush()
        self.current_event = Event(
            target_id=self.target.id,
            source_type="github",
            source_key="current-event",
            title="Commit in org/repo",
            raw_content='{"repo":"org/repo","message":"Improve platform"}',
            one_liner="Improve platform",
            collection_date=WEEK_START,
            event_date=datetime(2026, 11, 2, 10, tzinfo=timezone.utc),
            is_baseline=False,
            date_basis="source",
        )
        self.session.add(self.current_event)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def run_with(self, *, current=True, llm_result=None):
        with (
            patch("app.weekly.detector.embed_missing_events", return_value={"status": "no_work"}),
            patch("app.weekly.detector.build_weekly_input", return_value=input_payload(current)),
            patch("app.weekly.detector.run_weekly_llm", return_value=llm_result or llm_output()),
        ):
            result = run_weekly_analysis(
                self.session,
                target_id=self.target.id,
                week_start=WEEK_START,
                config=WeeklyConfig(),
            )
        return result, None

    def test_normal_week_verifies_scores_and_persists(self):
        proposal = {
            "title": "Platform investment",
            "description": "The code activity shows platform changes.",
            "connection_note": "The event is current-week evidence.",
            "sources": ["github"],
            "cited_event_ids": [self.current_event.id],
        }
        result, _ = self.run_with(llm_result=llm_output([proposal]))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["signal_count"], 1)
        stored = self.session.query(WeeklySignal).one()
        self.assertEqual(stored.score, 1.5)
        self.assertEqual(stored.signal_types, ["isolated"])
        self.assertEqual(stored.sources, ["github"])
        self.assertEqual(stored.claimed_sources, ["github"])

    def test_zero_event_week_skips_llm_and_stores_deterministic_low_activity(self):
        self.session.query(Event).delete()
        self.session.commit()
        with (
            patch("app.weekly.detector.embed_missing_events", return_value={"status": "no_work"}),
            patch("app.weekly.detector.build_weekly_input", return_value=input_payload(current=False)),
            patch("app.weekly.detector.run_weekly_llm") as llm,
        ):
            result = run_weekly_analysis(self.session, target_id=self.target.id, week_start=WEEK_START)
        llm.assert_not_called()
        self.assertEqual(result["status"], "low_activity")
        stored = self.session.query(WeeklyResult).one()
        self.assertIn("Low activity this week", stored.summary)
        self.assertEqual(stored.status, "low_activity")

    def test_no_valid_signals_stores_deterministic_no_signals_result(self):
        result, _ = self.run_with(llm_result=llm_output([], "Model says something"))
        self.assertEqual(result["status"], "no_signals")
        stored = self.session.query(WeeklyResult).one()
        self.assertIn("No verified signals this week", stored.summary)

    def test_all_rejected_signals_are_audited_but_not_published(self):
        proposal = {
            "title": "Invented signal",
            "description": "Unsupported",
            "connection_note": "Missing ID",
            "sources": ["github"],
            "cited_event_ids": [99999],
        }
        result, _ = self.run_with(llm_result=llm_output([proposal]))
        self.assertEqual(result["status"], "no_signals")
        self.assertEqual(result["signal_count"], 0)
        run = self.session.query(WeeklyRun).one()
        self.assertEqual(run.rejected_signals_json[0]["rejection_reasons"][0], "unknown_event_id")
        self.assertEqual(self.session.query(WeeklySignal).count(), 0)

    def test_retryable_and_nonretryable_llm_failures_are_recorded(self):
        cases = [(True, True), (False, False)]
        for attempt, (retryable, expected) in enumerate(cases, start=1):
            with (
                patch("app.weekly.detector.embed_missing_events", return_value={"status": "no_work"}),
                patch("app.weekly.detector.build_weekly_input", return_value=input_payload()),
                patch("app.weekly.detector.run_weekly_llm", side_effect=WeeklyLLMError(
                    "provider failed", retryable=retryable, attempts=1
                )),
            ):
                result = run_weekly_analysis(self.session, target_id=self.target.id, week_start=WEEK_START)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["retryable"], expected)
            self.assertEqual(self.session.query(WeeklyRun).filter_by(attempt=attempt).one().status, "failed")
        self.assertEqual(self.session.query(WeeklyResult).count(), 0)

    def test_embedding_failure_is_retryable_and_does_not_call_llm(self):
        with (
            patch("app.weekly.detector.embed_missing_events", return_value={"status": "failed", "error_type": "OSError"}),
            patch("app.weekly.detector.build_weekly_input") as builder,
        ):
            result = run_weekly_analysis(self.session, target_id=self.target.id, week_start=WEEK_START)
        builder.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["retryable"])

    def test_embedding_schema_failure_is_not_retried_and_is_actionable(self):
        with patch(
            "app.weekly.detector.embed_missing_events",
            return_value={
                "status": "failed",
                "error_type": "EmbeddingSchemaError",
                "error_message": "Run `python -m alembic upgrade head` before retrying.",
            },
        ), patch("app.weekly.detector.build_weekly_input") as builder:
            result = run_weekly_analysis(
                self.session,
                target_id=self.target.id,
                week_start=WEEK_START,
            )
        builder.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["retryable"])
        self.assertIn("alembic upgrade head", result["error"])

    def test_embedding_provider_failure_surfaces_actionable_error(self):
        with patch(
            "app.weekly.detector.embed_missing_events",
            return_value={
                "status": "failed",
                "error_type": "EmbeddingProviderError",
                "error_message": "Voyage embeddings request failed with HTTP 401; check VOYAGE_API_KEY.",
                "retryable": False,
            },
        ), patch("app.weekly.detector.build_weekly_input") as builder:
            result = run_weekly_analysis(
                self.session,
                target_id=self.target.id,
                week_start=WEEK_START,
            )
        builder.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["retryable"])
        self.assertIn("HTTP 401", result["error"])
        self.assertIn("VOYAGE_API_KEY", result["error"])

    def test_rerunning_week_replaces_result_and_accumulates_runs(self):
        first, _ = self.run_with(llm_result=llm_output([], "First"))
        second, _ = self.run_with(llm_result=llm_output([], "Second"))
        self.assertEqual(first["result_id"], second["result_id"])
        self.assertEqual(self.session.query(WeeklyResult).count(), 1)
        self.assertEqual(self.session.query(WeeklyRun).count(), 2)
        self.assertEqual(self.session.query(WeeklyResult).one().summary.split(".")[0], "No verified signals this week")

    def test_busy_lock_rejects_overlap_without_creating_run(self):
        from app.weekly.detector import WEEKLY_LOCK

        WEEKLY_LOCK.acquire()
        try:
            with self.assertRaises(WeeklyRunBusy):
                run_weekly_analysis(self.session, target_id=self.target.id, week_start=WEEK_START)
        finally:
            WEEKLY_LOCK.release()
        self.assertEqual(self.session.query(WeeklyRun).count(), 0)


if __name__ == "__main__":
    unittest.main()
