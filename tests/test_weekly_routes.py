import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.targets.models import Target
from app.weekly import routes
from app.weekly.models import WeeklyRun
from app.weekly.repository import complete_run, create_run


WEEK_START = date(2026, 11, 2)


class WeeklyRouteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.owner = User(email="routes-owner@example.invalid", password_hash="x")
        self.other = User(email="routes-other@example.invalid", password_hash="x")
        self.session.add_all([self.owner, self.other])
        self.session.flush()
        self.target = Target(
            user_id=self.owner.id,
            name="Route target",
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

    def add_result(self):
        run = create_run(self.session, target_id=self.target.id, week_start=WEEK_START, attempt=1)
        result = complete_run(
            self.session,
            run,
            weekly_input={"metadata": {"history_weeks_available": 0, "prompt_version": "v1", "estimated_tokens": 5}},
            llm_result={
                "output": {"summary": "Summary", "data_limitations": []},
                "raw_output": '{"summary":"Summary"}',
                "provider": "groq",
                "model": "model",
                "prompt_version": "v1",
                "usage": {"total_tokens": 10},
            },
            source_states={},
            exact_counts=[],
            signals=[],
        )
        return run, result

    def test_run_endpoint_checks_owner_ready_state_and_monday(self):
        response = {"status": "ok"}
        with patch("app.weekly.routes.run_weekly_analysis", return_value=response) as run:
            self.assertEqual(routes.run_weekly(
                target_id=self.target.id,
                week_start=WEEK_START,
                current_user=self.owner,
                db=self.session,
            ), response)
            run.assert_called_once()

        with self.assertRaises(HTTPException) as missing_owner:
            routes.run_weekly(
                target_id=self.target.id,
                week_start=WEEK_START,
                current_user=self.other,
                db=self.session,
            )
        self.assertEqual(missing_owner.exception.status_code, 404)

        with self.assertRaises(HTTPException) as bad_week:
            routes.run_weekly(
                target_id=self.target.id,
                week_start=date(2026, 11, 3),
                current_user=self.owner,
                db=self.session,
            )
        self.assertEqual(bad_week.exception.status_code, 422)

    def test_run_endpoint_rejects_non_ready_target_and_busy_lock(self):
        self.target.status = "building"
        self.session.commit()
        with self.assertRaises(HTTPException) as not_ready:
            routes.run_weekly(
                target_id=self.target.id,
                week_start=WEEK_START,
                current_user=self.owner,
                db=self.session,
            )
        self.assertEqual(not_ready.exception.status_code, 409)

        self.target.status = "ready"
        self.session.commit()
        with patch("app.weekly.routes.run_weekly_analysis", side_effect=routes.WeeklyRunBusy("busy")):
            with self.assertRaises(HTTPException) as busy:
                routes.run_weekly(
                    target_id=self.target.id,
                    week_start=WEEK_START,
                    current_user=self.owner,
                    db=self.session,
                )
        self.assertEqual(busy.exception.status_code, 409)

    def test_list_get_and_run_history_are_owner_scoped(self):
        run, result = self.add_result()
        listed = routes.list_weekly_results(self.target.id, self.owner, self.session)
        self.assertEqual(listed["items"][0]["id"], result.id)

        detail = routes.get_weekly_result(self.target.id, WEEK_START, self.owner, self.session)
        self.assertEqual(detail["summary"], "Summary")
        self.assertEqual(detail["signals"], [])

        minimal = routes.get_weekly_runs(self.target.id, WEEK_START, False, self.owner, self.session)
        self.assertNotIn("input_json", minimal["items"][0])
        expanded = routes.get_weekly_runs(self.target.id, WEEK_START, True, self.owner, self.session)
        self.assertEqual(expanded["items"][0]["input_json"]["metadata"]["prompt_version"], "v1")
        self.assertEqual(expanded["items"][0]["id"], run.id)

        with self.assertRaises(HTTPException) as foreign_list:
            routes.list_weekly_results(self.target.id, self.other, self.session)
        self.assertEqual(foreign_list.exception.status_code, 404)
        with self.assertRaises(HTTPException) as foreign_detail:
            routes.get_weekly_result(self.target.id, WEEK_START, self.other, self.session)
        self.assertEqual(foreign_detail.exception.status_code, 404)
        with self.assertRaises(HTTPException) as foreign_runs:
            routes.get_weekly_runs(self.target.id, WEEK_START, False, self.other, self.session)
        self.assertEqual(foreign_runs.exception.status_code, 404)

    def test_missing_weekly_result_and_invalid_week_are_rejected(self):
        with self.assertRaises(HTTPException) as missing:
            routes.get_weekly_result(self.target.id, WEEK_START, self.owner, self.session)
        self.assertEqual(missing.exception.status_code, 404)
        with self.assertRaises(HTTPException) as invalid:
            routes.get_weekly_runs(self.target.id, date(2026, 11, 3), False, self.owner, self.session)
        self.assertEqual(invalid.exception.status_code, 422)

    def test_weekly_routes_are_registered(self):
        from app.main import app

        paths = app.openapi()["paths"]
        self.assertIn("/targets/{target_id}/weekly/run", paths)
        self.assertIn("/targets/{target_id}/weekly", paths)
        self.assertIn("/targets/{target_id}/weekly/{week_start}", paths)
        self.assertIn("/targets/{target_id}/weekly/{week_start}/runs", paths)


if __name__ == "__main__":
    unittest.main()
