import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.intelligence_desk import routes
from app.targets.models import Target
from app.weekly.models import WeeklyLead, WeeklyLeadRun


WEEK_START = date(2026, 11, 16)


class IntelligenceRouteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.owner = User(email="scout-owner@example.invalid", password_hash="x")
        self.session.add(self.owner)
        self.session.flush()
        self.target = Target(
            user_id=self.owner.id,
            name="Scout target",
            website_url="https://company.example",
            youtube_url="https://youtube.com/@company",
            status="ready",
        )
        self.session.add(self.target)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_run_passes_only_weekly_leads_to_background_scout(self):
        lead_run = WeeklyLeadRun(
            target_id=self.target.id,
            week_start_date=WEEK_START,
            attempt=1,
            status="succeeded",
            is_current=True,
            input_event_ids=[101],
        )
        self.session.add(lead_run)
        self.session.flush()
        self.session.add(WeeklyLead(
            run_id=lead_run.id,
            target_id=self.target.id,
            week_start_date=WEEK_START,
            lead_index=0,
            description="Company expanded its open-source runtime.",
            source_event_ids=[101],
            embedding=[0.0] * 256,
            embedding_model_id="test-model",
        ))
        self.session.commit()

        background = BackgroundTasks()
        with patch("app.intelligence_desk.routes.last_closed_week", return_value=WEEK_START):
            response = routes.start_intelligence_run(
                target_id=self.target.id,
                background_tasks=background,
                current_user=self.owner,
                db=self.session,
            )

        self.assertEqual(response["week_start"], WEEK_START)
        task_args = background.tasks[0].args
        self.assertEqual(task_args[3], self.target.website_url)
        self.assertEqual(task_args[-2], [{
            "id": 1,
            "description": "Company expanded its open-source runtime.",
            "source_event_ids": [101],
        }])
        self.assertEqual(task_args[-1], "available")

    def test_missing_weekly_lead_run_does_not_block_scout(self):
        background = BackgroundTasks()
        with patch("app.intelligence_desk.routes.last_closed_week", return_value=WEEK_START):
            response = routes.start_intelligence_run(
                target_id=self.target.id,
                background_tasks=background,
                current_user=self.owner,
                db=self.session,
            )

        self.assertEqual(response["status"], "queued")
        self.assertEqual(background.tasks[0].args[-2], [])
        self.assertEqual(background.tasks[0].args[-1], "unavailable")

    def test_execution_result_reports_actual_tool_status_and_review_without_rejecting_findings(self):
        tool_result = SimpleNamespace(
            type="tool",
            name="read_webpage",
            content='{"status":"checked","url":"https://company.example/news","title":"News","published_at":null,"text":"long page body"}',
            tool_calls=[],
        )
        final = SimpleNamespace(
            type="ai",
            content='{"summary":"Useful context","findings":[{"observation":"Undated update","source_urls":["https://company.example/news"],"published_at":null}]}',
            tool_calls=[],
        )
        result = routes._execution_result({
            "messages": [tool_result, final],
        }, date(2026, 11, 16), date(2026, 11, 22))

        self.assertEqual(result["trace"][0]["status"], "checked")
        self.assertNotIn("text", result["trace"][0])
        self.assertEqual(result["review"]["findings"][0]["date_status"], "unknown")
        self.assertEqual(
            result["review"]["findings"][0]["citation_status"][0]["status"],
            "returned_by_checked_tool",
        )
        self.assertIn("Undated update", result["final_output"])

    def test_review_marks_out_of_period_publication_and_keeps_model_output(self):
        final = SimpleNamespace(
            type="ai",
            content='{"summary":"Context","findings":[{"source_urls":[],"published_at":"2026-11-22T20:00:00Z"}]}',
            tool_calls=[],
        )
        result = routes._execution_result({
            "messages": [final],
        }, date(2026, 11, 16), date(2026, 11, 22))
        # 20:00Z is Nov 23 in Kolkata, beyond the selected week.
        self.assertEqual(result["review"]["findings"][0]["date_status"], "outside_period")
        self.assertEqual(result["final_output"], final.content)

    def test_unparseable_final_json_is_preserved_and_only_review_is_unavailable(self):
        final = SimpleNamespace(type="ai", content="Useful but not JSON", tool_calls=[])
        result = routes._execution_result({
            "messages": [final],
        }, WEEK_START, WEEK_START.replace(day=22))
        self.assertEqual(result["final_output"], "Useful but not JSON")
        self.assertFalse(result["review"]["json_parsed"])


if __name__ == "__main__":
    unittest.main()
