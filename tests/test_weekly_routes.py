import unittest
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.targets.models import Target
from app.weekly import routes
from app.weekly.models import DetectedSignal, SignalDetectionRun
from app.weekly.repository import complete_lead_run, create_lead_run


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

    def add_run(self):
        run = create_lead_run(
            self.session,
            target_id=self.target.id,
            week_start=WEEK_START,
            attempt=1,
            input_event_ids=[10],
            input_json={"week_start": WEEK_START.isoformat(), "events": []},
            input_token_estimate=3,
        )
        return complete_lead_run(
            self.session,
            run,
            output_json={"leads": [{
                "description": "Description",
                "source_event_ids": [10],
            }]},
            raw_output='{"leads":[]}',
            llm_provider="groq",
            llm_model="model",
            embedding_model_id="voyage-4-lite",
            prompt_version="weekly-leads-v2",
            leads=[{
                "description": "Description",
                "source_event_ids": [10],
            }],
            vectors=[[0.1] * 256],
        )

    def test_run_endpoint_checks_owner_ready_state_and_monday(self):
        from unittest.mock import patch

        response = {
            "status": "succeeded",
            "llm_response": '{"leads":[]}',
            "error": None,
        }
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
        from unittest.mock import patch

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

    def test_run_endpoint_returns_gateway_error_for_saved_failed_run(self):
        from unittest.mock import patch

        failure = {
            "status": "failed",
            "run_id": 42,
            "retryable": False,
            "error": "Weekly input exceeds the configured request byte limit.",
            "llm_response": None,
        }
        with patch("app.weekly.routes.run_weekly_analysis", return_value=failure):
            with self.assertRaises(HTTPException) as raised:
                routes.run_weekly(
                    target_id=self.target.id,
                    week_start=WEEK_START,
                    current_user=self.owner,
                    db=self.session,
                )
        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.detail, failure)

    def test_list_detail_and_run_history_are_owner_scoped(self):
        run = self.add_run()
        listed = routes.list_weekly_leads(self.target.id, self.owner, self.session)
        self.assertEqual(listed["items"][0]["id"], run.id)
        self.assertEqual(listed["items"][0]["leads"][0]["description"], "Description")
        self.assertNotIn("title", listed["items"][0]["leads"][0])

        detail = routes.get_weekly_lead_run(
            self.target.id, WEEK_START, self.owner, self.session
        )
        self.assertEqual(detail["leads"][0]["source_event_ids"], [10])
        self.assertNotIn("title", detail["leads"][0])

        minimal = routes.get_weekly_runs(
            self.target.id, WEEK_START, False, self.owner, self.session
        )
        self.assertNotIn("input_json", minimal["items"][0])
        expanded = routes.get_weekly_runs(
            self.target.id, WEEK_START, True, self.owner, self.session
        )
        self.assertEqual(expanded["items"][0]["input_json"]["week_start"], WEEK_START.isoformat())
        self.assertEqual(expanded["items"][0]["id"], run.id)

        with self.assertRaises(HTTPException) as foreign_list:
            routes.list_weekly_leads(self.target.id, self.other, self.session)
        self.assertEqual(foreign_list.exception.status_code, 404)
        with self.assertRaises(HTTPException) as foreign_detail:
            routes.get_weekly_lead_run(self.target.id, WEEK_START, self.other, self.session)
        self.assertEqual(foreign_detail.exception.status_code, 404)
        with self.assertRaises(HTTPException) as foreign_runs:
            routes.get_weekly_runs(self.target.id, WEEK_START, False, self.other, self.session)
        self.assertEqual(foreign_runs.exception.status_code, 404)

    def test_missing_week_and_invalid_week_are_rejected(self):
        with self.assertRaises(HTTPException) as missing:
            routes.get_weekly_lead_run(
                self.target.id, WEEK_START, self.owner, self.session
            )
        self.assertEqual(missing.exception.status_code, 404)
        with self.assertRaises(HTTPException) as invalid:
            routes.get_weekly_runs(
                self.target.id, date(2026, 11, 3), False, self.owner, self.session
            )
        self.assertEqual(invalid.exception.status_code, 422)

    def test_signal_run_endpoint_is_manual_and_owner_scoped(self):
        from unittest.mock import patch

        response = {"status": "succeeded", "signal_count": 1}
        with patch("app.weekly.routes.run_signal_detection", return_value=response) as run:
            self.assertEqual(routes.run_signals(
                self.target.id, WEEK_START, self.owner, self.session
            ), response)
            run.assert_called_once()

        with self.assertRaises(HTTPException) as foreign:
            routes.run_signals(self.target.id, WEEK_START, self.other, self.session)
        self.assertEqual(foreign.exception.status_code, 404)

        with self.assertRaises(HTTPException) as invalid:
            routes.run_signals(
                self.target.id, date(2026, 11, 3), self.owner, self.session
            )
        self.assertEqual(invalid.exception.status_code, 422)

    def test_signal_result_endpoint_returns_owner_scoped_run_and_signals(self):
        run = SignalDetectionRun(
            target_id=self.target.id,
            week_start_date=WEEK_START,
            attempt=1,
            status="succeeded",
            is_current=True,
            retryable=False,
            output_json={"signals": [], "no_matches_reason": None},
        )
        self.session.add(run)
        self.session.flush()
        self.session.add(DetectedSignal(
            run_id=run.id,
            target_id=self.target.id,
            week_start_date=WEEK_START,
            signal_index=0,
            title="New platform investment",
            description="Current leads indicate a new platform initiative.",
            signal_types=["emerging"],
            confidence="medium",
            confidence_rationale="Supported by current-week evidence.",
            current_lead_ids=[10],
            historical_lead_ids=[],
        ))
        self.session.commit()

        response = routes.get_weekly_signals(
            self.target.id, WEEK_START, self.owner, self.session
        )
        self.assertEqual(response["signals"][0]["signal_types"], ["emerging"])

        with self.assertRaises(HTTPException) as foreign:
            routes.get_weekly_signals(
                self.target.id, WEEK_START, self.other, self.session
            )
        self.assertEqual(foreign.exception.status_code, 404)

    def test_weekly_routes_are_registered(self):
        from app.main import app

        paths = app.openapi()["paths"]
        self.assertIn("/targets/{target_id}/weekly/run", paths)
        self.assertIn("/targets/{target_id}/weekly", paths)
        self.assertIn("/targets/{target_id}/weekly/{week_start}", paths)
        self.assertIn("/targets/{target_id}/weekly/{week_start}/runs", paths)
        self.assertIn("/targets/{target_id}/weekly/signals/run", paths)
        self.assertIn("/targets/{target_id}/weekly/{week_start}/signals", paths)
        self.assertIn("/targets/{target_id}/weekly/{week_start}/signals/runs", paths)


if __name__ == "__main__":
    unittest.main()
