import unittest
from datetime import date
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


if __name__ == "__main__":
    unittest.main()
