import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.daily import collectors
from app.events.models import DailySourceSummary, Event
from app.targets.models import Target


class DailyCollectorTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.user = User(email="daily-test@example.com", password_hash="test")
        self.session.add(self.user)
        self.session.flush()
        self.target = Target(
            user_id=self.user.id,
            name="Test target",
            website_url="https://example.com",
            github_url="https://github.com/example/project",
            ats_url=None,
            blog_url=None,
            web_social_url=None,
            status="ready",
        )
        self.session.add(self.target)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def commit(sha, timestamp):
        return {
            "sha": sha,
            "repo": "example/project",
            "date": timestamp,
            "message": f"Commit {sha}",
            "url": f"https://github.com/example/project/commit/{sha}",
        }

    def test_date_window_uses_asia_kolkata_calendar_day(self):
        collection_date = date(2026, 11, 5)
        candidates = [
            self.commit("before", "2026-11-04T18:29:59Z"),
            self.commit("at-start", "2026-11-04T18:30:00Z"),
            self.commit("at-end", "2026-11-05T18:29:59Z"),
            self.commit("after", "2026-11-05T18:30:00Z"),
        ]

        with patch.object(collectors, "get_new_commits", return_value=candidates) as fetch:
            collectors.collect_target_events(self.session, self.target, collection_date)

        self.assertEqual(
            fetch.call_args.args[1],
            datetime(2026, 11, 4, 18, 30, tzinfo=timezone.utc),
        )
        saved = self.session.query(Event).all()
        self.assertEqual({event.source_key for event in saved}, {"at-start", "at-end"})
        self.assertTrue(all(event.collection_date == collection_date for event in saved))

    def test_repeated_collection_deduplicates_same_commit(self):
        item = self.commit("same-commit", "2026-11-05T08:00:00Z")
        with patch.object(collectors, "get_new_commits", return_value=[item]):
            first = collectors.collect_target_events(
                self.session, self.target, date(2026, 11, 5)
            )
            second = collectors.collect_target_events(
                self.session, self.target, date(2026, 11, 5)
            )

        self.assertEqual(first["sources"]["github"]["new_events"], 1)
        self.assertEqual(second["sources"]["github"]["new_events"], 0)
        self.assertEqual(self.session.query(Event).count(), 1)
        summary = self.session.query(DailySourceSummary).filter_by(
            target_id=self.target.id,
            collection_date=date(2026, 11, 5),
            source_type="github",
        ).one()
        self.assertEqual(summary.event_count, 1)

    def test_first_web_snapshot_is_baseline_and_next_change_is_event(self):
        self.target.web_social_url = "https://example.com/news"
        self.session.commit()
        page = {"content": "Initial page", "url": self.target.web_social_url}

        with patch.object(collectors, "get_web_social_content", side_effect=lambda *a, **k: page):
            first = collectors.collect_target_events(
                self.session, self.target, date(2026, 11, 5)
            )
            page["content"] = "Updated page"
            second = collectors.collect_target_events(
                self.session, self.target, date(2026, 11, 5)
            )

        events = self.session.query(Event).filter_by(
            target_id=self.target.id, source_type="web_social"
        ).order_by(Event.id).all()
        self.assertTrue(events[0].is_baseline)
        self.assertFalse(events[1].is_baseline)
        self.assertEqual(first["sources"]["web_social"]["baseline_events"], 1)
        self.assertEqual(first["sources"]["web_social"]["event_count"], 0)
        self.assertEqual(second["sources"]["web_social"]["new_events"], 1)
        self.assertEqual(second["sources"]["web_social"]["event_count"], 1)


if __name__ == "__main__":
    unittest.main()
