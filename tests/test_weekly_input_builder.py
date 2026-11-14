import unittest
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.events.models import DailySourceSummary, Event
from app.kundali.models import Kundali
from app.targets.models import Target
from app.weekly.config import WeeklyConfig
from app.weekly.input_builder import (
    WeeklyInputError,
    _all_current_event_ids,
    _compact_until_fit,
    build_weekly_input,
    estimate_tokens,
)


WEEK_START = date(2026, 11, 9)


class WeeklyInputBuilderTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        user = User(email="input-test@example.invalid", password_hash="test")
        self.session.add(user)
        self.session.flush()
        self.target = Target(
            user_id=user.id,
            name="Input test target",
            website_url="https://example.invalid",
            github_url="https://github.com/example/repo",
            ats_url="https://jobs.example.invalid",
            blog_url="https://example.invalid/blog",
            web_social_url="https://example.invalid/news",
            status="ready",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.session.add(self.target)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def add_event(self, day, source="github", *, baseline=False, date_basis="source", key=None, text="Relevant product work"):
        event = Event(
            target_id=self.target.id,
            source_type=source,
            source_key=key or f"{source}-{day}-{self.session.query(Event).count()}",
            title="Commit in example/repo" if source == "github" else f"{source} update",
            url="https://example.invalid/item",
            raw_content=text,
            one_liner=text,
            collection_date=day,
            event_date=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
            is_baseline=baseline,
            date_basis=date_basis,
        )
        self.session.add(event)
        self.session.commit()
        return event

    @staticmethod
    def fake_retrieval(examples=None):
        return {
            "queries_used": 1,
            "examples": examples or [],
            "github_groups": [],
            "week_match_counts": {"2026-11-02": 1},
        }

    def test_snapshot_shape_is_stable_and_counts_are_exact(self):
        self.add_event(WEEK_START - timedelta(days=1), "ats", key="history")
        current = self.add_event(WEEK_START, "blog", key="current")
        payload = build_weekly_input(
            self.session,
            target_id=self.target.id,
            week_start=WEEK_START,
            today=date(2026, 11, 17),
            retrieval_data=self.fake_retrieval(),
        )
        self.assertEqual(
            list(payload),
            ["metadata", "sources", "weekly_counts", "current_week_events", "retrieved_history", "baseline_context"],
        )
        self.assertEqual(list(payload["metadata"]), [
            "target_id", "week_start", "week_end", "timezone", "prompt_version",
            "history_weeks_available", "history_window_weeks", "compaction_level",
            "compaction_log", "estimated_tokens",
        ])
        self.assertEqual(payload["metadata"]["history_weeks_available"], 1)
        self.assertEqual(payload["weekly_counts"][-1]["sources"]["blog"], 1)
        self.assertEqual(payload["weekly_counts"][-1]["sources"]["ats"], 0)
        self.assertEqual(payload["current_week_events"][0]["event_id"], current.id)
        self.assertGreater(payload["metadata"]["estimated_tokens"], 0)
        self.assertLessEqual(payload["metadata"]["estimated_tokens"], WeeklyConfig().input_token_budget)

    def test_cold_start_is_zero_and_first_observed_is_labeled(self):
        first_seen = self.add_event(WEEK_START + timedelta(days=2), "ats", date_basis="first_observed")
        payload = build_weekly_input(
            self.session,
            target_id=self.target.id,
            week_start=WEEK_START,
            today=date(2026, 11, 17),
            retrieval_data=self.fake_retrieval(),
        )
        self.assertEqual(payload["metadata"]["history_weeks_available"], 0)
        self.assertEqual(payload["current_week_events"][0]["event_id"], first_seen.id)
        self.assertEqual(payload["current_week_events"][0]["date_basis"], "first_observed")

    def test_baseline_is_separate_context_not_a_current_event(self):
        baseline = self.add_event(WEEK_START, baseline=True, key="baseline")
        self.session.add(Kundali(
            target_id=self.target.id,
            tech_stack={"Python": 10, "Rust": 20},
            cadence_baseline={"0": 5, "51": 2},
            ats_postings=[{"title": "Platform Engineer", "location": "Remote"}],
            blog_status=[{"title": "Platform launch"}],
        ))
        self.session.commit()
        payload = build_weekly_input(
            self.session,
            target_id=self.target.id,
            week_start=WEEK_START,
            today=date(2026, 11, 17),
            retrieval_data=self.fake_retrieval(),
        )
        self.assertEqual(payload["current_week_events"], [])
        self.assertNotIn(baseline.id, _all_current_event_ids(payload["current_week_events"]))
        baseline_context = payload["baseline_context"]
        self.assertTrue(baseline_context["available"])
        self.assertEqual(baseline_context["tech_stack_top8_by_bytes"], {"Rust": 20, "Python": 10})
        self.assertEqual(baseline_context["commit_cadence_52w"]["total_commits"], 7)
        self.assertEqual(baseline_context["active_job_snapshot"][0]["title"], "Platform Engineer")
        self.assertIn("not weekly event evidence", baseline_context["note"])

    def test_all_current_events_survive_staged_compaction(self):
        current_events = [
            {
                "event_id": event_id,
                "source": "github",
                "collection_date": WEEK_START.isoformat(),
                "date_basis": "source",
                "title": "Commit in org/repo",
                "one_liner": "A meaningful change",
                "text": "Repository: org/repo Commit: " + ("long detail " * 100),
            }
            for event_id in range(1, 5)
        ]
        examples = [
            {
                "event_id": event_id,
                "source": "blog",
                "collection_date": (WEEK_START - timedelta(days=7)).isoformat(),
                "date_basis": "source",
                "similarity": 1.0 - event_id / 20,
                "text": "history details " * 100,
                "query_event_ids": [1],
            }
            for event_id in range(100, 112)
        ]
        payload = {
            "metadata": {"target_id": 1},
            "sources": [],
            "weekly_counts": [],
            "current_week_events": current_events,
            "retrieved_history": {
                "queries_used": 4,
                "examples": examples,
                "github_groups": [],
                "week_match_counts": {"2026-11-02": 12},
            },
            "baseline_context": {
                "available": True,
                "note": "baseline",
                "active_job_snapshot": [],
                "blog_snapshot": [],
            },
        }
        config = WeeklyConfig(input_token_budget=1000, retrieval_budget_share=0.30)
        fitted, token_estimate, compaction_log = _compact_until_fit(payload, config)
        self.assertLessEqual(token_estimate, config.input_token_budget)
        self.assertEqual(compaction_log[0], "shorten_text")
        self.assertIn("collapse_github_by_repo", compaction_log)
        self.assertIn("reduce_retrieval_examples", compaction_log)
        self.assertEqual(_all_current_event_ids(fitted["current_week_events"]), {1, 2, 3, 4})
        self.assertTrue(fitted["retrieved_history"]["omitted_example_event_ids"])

    def test_over_budget_fails_explicitly_without_dropping_current_events(self):
        payload = {
            "metadata": {},
            "sources": [],
            "weekly_counts": [],
            "current_week_events": [
                {"event_id": n, "source": "ats", "text": "role " * 100}
                for n in range(100)
            ],
            "retrieved_history": {"queries_used": 0, "examples": [], "github_groups": [], "week_match_counts": {}},
            "baseline_context": {"active_job_snapshot": [], "blog_snapshot": []},
        }
        with self.assertRaises(WeeklyInputError) as raised:
            _compact_until_fit(payload, WeeklyConfig(input_token_budget=100, retrieval_budget_share=0.5))
        self.assertEqual(raised.exception.reason, "input_over_budget")
        self.assertGreater(raised.exception.token_estimate, raised.exception.token_budget)

    def test_non_monday_and_unknown_target_are_rejected(self):
        with self.assertRaises(ValueError):
            build_weekly_input(self.session, target_id=self.target.id, week_start=WEEK_START + timedelta(days=1))
        with self.assertRaisesRegex(ValueError, "target_not_found"):
            build_weekly_input(self.session, target_id=99999, week_start=WEEK_START)


if __name__ == "__main__":
    unittest.main()
