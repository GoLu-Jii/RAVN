import json
import unittest
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.embeddings.model import EMBEDDING_DIMENSION
from app.events.models import Event
from app.kundali.models import Kundali
from app.targets.models import Target
from app.weekly.replay import render_replay_report, replay_weeks


WEEK_START = date(2026, 11, 9)


class WeeklyReplayTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        user = User(email="replay-test@example.invalid", password_hash="test")
        self.session.add(user)
        self.session.flush()
        self.target = Target(
            user_id=user.id,
            name="Replay target",
            website_url="https://example.invalid",
            github_url="https://github.com/example/repo",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.session.add(self.target)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def add_event(self, day, title):
        event = Event(
            target_id=self.target.id,
            source_type="github",
            source_key=title,
            title=title,
            url="https://example.invalid/event",
            raw_content=title,
            one_liner=title,
            collection_date=day,
            event_date=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
            is_baseline=False,
            date_basis="source",
        )
        self.session.add(event)
        self.session.commit()
        return event

    @staticmethod
    def fake_embedder(texts):
        vectors = []
        for text in texts:
            vector = [0.0] * EMBEDDING_DIMENSION
            vector[0 if "current" in text.lower() else 1] = 1.0
            vectors.append(vector)
        return vectors

    def test_replay_excludes_events_and_kundali_created_after_historical_cutoff(self):
        current = self.add_event(WEEK_START, "Current week event")
        future = self.add_event(WEEK_START + timedelta(days=7), "Future week event")
        self.session.add(Kundali(
            target_id=self.target.id,
            tech_stack={"FutureTech": 1},
            created_at=datetime(2026, 11, 17, tzinfo=timezone.utc),
        ))
        self.session.commit()

        replay = replay_weeks(
            self.session,
            target_id=self.target.id,
            weeks=1,
            now=datetime(2026, 11, 18, tzinfo=timezone.utc),
            embedder=self.fake_embedder,
        )

        weekly_input = replay["weeks"][0]["input"]
        self.assertEqual(
            [event["event_id"] for event in weekly_input["current_week_events"]],
            [current.id],
        )
        self.assertNotIn(future.id, [
            event["event_id"] for event in weekly_input["retrieved_history"]["examples"]
        ])
        self.assertFalse(weekly_input["baseline_context"]["available"])
        self.assertNotIn("FutureTech", json.dumps(weekly_input))
        self.assertEqual(
            self.session.query(Event).filter_by(target_id=self.target.id).count(), 2,
        )

    def test_fake_replay_generates_markdown_report_without_provider_call(self):
        replay = replay_weeks(
            self.session,
            target_id=self.target.id,
            weeks=1,
            now=datetime(2026, 11, 18, tzinfo=timezone.utc),
        )
        report = render_replay_report(replay)
        self.assertIn("# RAVN weekly replay", report)
        self.assertIn("fake LLM", report)
        self.assertIn("N/A (no citations proposed)", report)
        self.assertIn("Low activity", report)
        self.assertEqual(replay["weeks"][0]["signal_count"], 0)


if __name__ == "__main__":
    unittest.main()
