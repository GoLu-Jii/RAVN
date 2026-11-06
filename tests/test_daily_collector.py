import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.daily import collectors
from app.events.models import DailySourceCheckpoint, DailySourceSummary, Event
from app.events.routes import get_daily_summaries, list_daily_events
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
            self.commit("at-end", "2026-11-05T18:29:00Z"),  # 23:59 IST
            self.commit("after", "2026-11-05T18:30:00Z"),  # 00:00 IST next day
        ]

        with patch.object(collectors, "get_new_commits", return_value=candidates) as fetch:
            collectors.collect_target_events(self.session, self.target, collection_date)
            collectors.collect_target_events(
                self.session, self.target, date(2026, 11, 6)
            )

        self.assertEqual(
            fetch.call_args_list[0].args[1],
            datetime(2026, 11, 4, 18, 30, tzinfo=timezone.utc),
        )
        saved = self.session.query(Event).all()
        self.assertEqual({event.source_key for event in saved}, {"at-start", "at-end", "after"})
        self.assertEqual(
            {event.source_key: event.collection_date for event in saved},
            {"at-start": collection_date, "at-end": collection_date, "after": date(2026, 11, 6)},
        )

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
        today = datetime.now(collectors.COLLECTION_TIMEZONE).date()
        self.target.web_social_url = "https://example.com/news"
        self.session.commit()
        page = {"content": "Initial page", "url": self.target.web_social_url}

        with patch.object(collectors, "get_web_social_content", side_effect=lambda *a, **k: page):
            first = collectors.collect_target_events(
                self.session, self.target, today
            )
            page["content"] = "Updated page"
            second = collectors.collect_target_events(
                self.session, self.target, today
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
        feed = list_daily_events(
            target_id=self.target.id,
            source_type=None,
            current_user=self.user,
            db=self.session,
            limit=50,
            offset=0,
        )
        self.assertEqual(feed["total"], 1)
        self.assertEqual(
            {item["id"] for item in feed["items"]},
            {event.id for event in self.session.query(Event).filter_by(is_baseline=False).all()},
        )
        self.assertEqual(events[0].date_basis, "first_observed")
        self.assertEqual(events[1].date_basis, "first_observed")

    def test_undated_ats_and_blog_lists_are_baselined_then_new_items_are_events(self):
        today = datetime.now(collectors.COLLECTION_TIMEZONE).date()
        self.target.ats_url = "https://careers.example.com/jobs"
        self.target.blog_url = "https://example.com/blog"
        self.session.commit()
        ats_items = [{
            "title": "Existing role",
            "location": "Remote",
            "url": "https://careers.example.com/jobs/existing",
            "posted_at": None,
        }]
        blog_items = [{
            "title": "Existing post",
            "link": "https://example.com/blog/existing",
            "published": None,
        }]

        with (
            patch.object(collectors, "get_ats_postings", side_effect=lambda *a, **k: list(ats_items)),
            patch.object(collectors, "get_blog_posts", side_effect=lambda *a, **k: list(blog_items)),
        ):
            first = collectors.collect_target_events(self.session, self.target, today)
            ats_items.append({
                "title": "New role",
                "location": "Remote",
                "url": "https://careers.example.com/jobs/new",
                "posted_at": None,
            })
            blog_items.append({
                "title": "New post",
                "link": "https://example.com/blog/new",
                "published": None,
            })
            second = collectors.collect_target_events(self.session, self.target, today)

        ats_events = self.session.query(Event).filter_by(
            target_id=self.target.id, source_type="ats"
        ).all()
        blog_events = self.session.query(Event).filter_by(
            target_id=self.target.id, source_type="blog"
        ).all()
        self.assertEqual({event.title: event.is_baseline for event in ats_events}, {
            "Existing role": True, "New role": False,
        })
        self.assertEqual({event.title: event.is_baseline for event in blog_events}, {
            "Existing post": True, "New post": False,
        })
        self.assertEqual(first["sources"]["ats"]["event_count"], 0)
        self.assertEqual(first["sources"]["blog"]["event_count"], 0)
        self.assertEqual(second["sources"]["ats"]["new_events"], 1)
        self.assertEqual(second["sources"]["blog"]["new_events"], 1)
        self.assertEqual(first["sources"]["ats"]["undated_items"], 1)
        self.assertEqual(first["sources"]["blog"]["undated_items"], 1)
        self.assertEqual(
            self.session.query(DailySourceSummary).filter_by(
                target_id=self.target.id, collection_date=today, source_type="ats",
            ).one().undated_items,
            1,
        )
        self.assertTrue(all(event.date_basis == "first_observed" for event in ats_events + blog_events))

    def test_lever_created_at_is_used_as_posted_date(self):
        self.target.ats_url = "https://jobs.lever.co/example"
        self.session.commit()
        published_at = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
        response = Mock(status_code=200)
        response.json.return_value = [{
            "text": "Backend Engineer",
            "categories": {"location": "Remote"},
            "hostedUrl": "https://jobs.lever.co/example/backend-engineer",
            "createdAt": int(published_at.timestamp() * 1000),
        }]

        with (
            patch("app.daily.fetchers.ats.httpx.get", return_value=response),
            patch.object(collectors, "_fetch_full_text", return_value="Role description"),
            patch.object(collectors, "get_new_commits", return_value=[]),
        ):
            result = collectors.collect_target_events(self.session, self.target, date(2026, 11, 5))

        event = self.session.query(Event).filter_by(
            target_id=self.target.id, source_type="ats"
        ).one()
        self.assertEqual(event.title, "Backend Engineer")
        self.assertEqual(event.event_date.replace(tzinfo=timezone.utc), published_at)
        self.assertEqual(event.date_basis, "source")
        self.assertEqual(result["sources"]["ats"]["new_events"], 1)

    def test_api_returns_date_basis_and_undated_count(self):
        self.target.ats_url = "https://careers.example.com/jobs"
        self.session.commit()
        today = datetime.now(collectors.COLLECTION_TIMEZONE).date()
        items = [{
            "title": "Undated role", "location": None,
            "url": "https://careers.example.com/jobs/undated", "posted_at": None,
        }]
        with patch.object(collectors, "get_ats_postings", side_effect=lambda *a, **k: list(items)):
            collectors.collect_target_events(self.session, self.target, today)
            items.append({
                "title": "New undated role", "location": None,
                "url": "https://careers.example.com/jobs/new", "posted_at": None,
            })
            collectors.collect_target_events(self.session, self.target, today)

        summaries = get_daily_summaries(
            target_id=self.target.id,
            collection_date=today,
            current_user=self.user,
            db=self.session,
        )
        ats_summary = next(source for source in summaries["sources"] if source["source_type"] == "ats")
        self.assertEqual(ats_summary["undated_items"], 1)
        feed = list_daily_events(
            target_id=self.target.id,
            source_type="ats",
            collection_date=today,
            limit=50,
            offset=0,
            current_user=self.user,
            db=self.session,
        )
        self.assertEqual(feed["items"][0]["date_basis"], "first_observed")

    def test_not_checked_items_are_reported_separately_from_undated(self):
        self.target.ats_url = "https://careers.example.com/jobs"
        self.session.commit()
        today = datetime.now(collectors.COLLECTION_TIMEZONE).date()
        items = [
            {
                "title": "Confirmed undated role", "location": None,
                "url": "https://careers.example.com/jobs/undated", "posted_at": None,
                "date_state": "undated",
            },
            {
                "title": "Unresolved role", "location": None,
                "url": "https://careers.example.com/jobs/unresolved", "posted_at": None,
                "date_state": "not_checked",
            },
        ]
        with patch.object(collectors, "get_ats_postings", return_value=items):
            result = collectors.collect_target_events(self.session, self.target, today)

        ats = result["sources"]["ats"]
        self.assertEqual(ats["undated_items"], 1)
        self.assertEqual(ats["not_checked_items"], 1)
        self.assertEqual(
            [event.title for event in self.session.query(Event).filter_by(source_type="ats").all()],
            ["Confirmed undated role"],
        )

    def test_known_ats_url_is_not_counted_as_undated(self):
        self.target.github_url = None
        self.target.ats_url = "https://careers.example.com/jobs"
        self.session.commit()
        today = datetime.now(collectors.COLLECTION_TIMEZONE).date()
        known_url = "https://careers.example.com/jobs/known"
        self.session.add(Event(
            target_id=self.target.id,
            source_type="ats",
            source_key="known-key",
            title="Previously stored role",
            url=known_url,
            raw_content="Existing event",
            one_liner="Existing event",
            collection_date=today,
            event_date=datetime.now(timezone.utc),
            tags=["hiring"],
            is_baseline=True,
            date_basis="first_observed",
        ))
        self.session.commit()
        listing = SimpleNamespace(
            status_code=200,
            text=(
                f'<a href="{known_url}">A previously stored engineering position</a>'
                '<a href="/jobs/new">A newly discovered engineering position</a>'
            ),
        )
        detail = SimpleNamespace(status_code=200, text="<html><body>No date published.</body></html>")
        with patch("app.daily.fetchers.ats.httpx.get", side_effect=[listing, detail]) as get:
            result = collectors.collect_target_events(self.session, self.target, today)

        ats = result["sources"]["ats"]
        self.assertEqual(ats["undated_items"], 1)
        self.assertEqual(ats["not_checked_items"], 1)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(self.session.query(Event).filter_by(source_type="ats").count(), 2)

    def test_known_blog_url_is_not_counted_as_undated(self):
        self.target.github_url = None
        self.target.blog_url = "https://example.com/blog"
        self.session.commit()
        today = datetime.now(collectors.COLLECTION_TIMEZONE).date()
        known_url = "https://example.com/blog/known-post"
        self.session.add(Event(
            target_id=self.target.id,
            source_type="blog",
            source_key="known-blog-key",
            title="Previously stored post",
            url=known_url,
            raw_content="Existing event",
            one_liner="Existing event",
            collection_date=today,
            event_date=datetime.now(timezone.utc),
            tags=["engineering"],
            is_baseline=True,
            date_basis="first_observed",
        ))
        self.session.commit()
        with patch.object(collectors, "get_blog_posts", return_value=[
            {"title": "Previously stored post", "link": known_url, "published": None},
            {"title": "A new undated article", "link": "https://example.com/blog/new", "published": None},
        ]):
            result = collectors.collect_target_events(self.session, self.target, today)

        blog = result["sources"]["blog"]
        self.assertEqual(blog["undated_items"], 1)
        self.assertEqual(blog["not_checked_items"], 1)
        self.assertEqual(self.session.query(Event).filter_by(source_type="blog").count(), 2)

    def test_failed_job_detail_fetch_does_not_create_event(self):
        self.target.github_url = None
        self.target.ats_url = "https://careers.example.com/jobs"
        self.session.commit()
        today = datetime.now(collectors.COLLECTION_TIMEZONE).date()
        listing = SimpleNamespace(
            status_code=200,
            text='<a href="/jobs/new">A newly discovered engineering position</a>',
        )
        with patch(
            "app.daily.fetchers.ats.httpx.get",
            side_effect=[listing, httpx.ConnectError("detail page unavailable")],
        ):
            result = collectors.collect_target_events(self.session, self.target, today)

        ats = result["sources"]["ats"]
        self.assertEqual(ats["undated_items"], 0)
        self.assertEqual(ats["not_checked_items"], 1)
        self.assertEqual(self.session.query(Event).filter_by(source_type="ats").count(), 0)

    def test_overflow_jobs_are_not_stored_or_counted_undated(self):
        self.target.github_url = None
        self.target.ats_url = "https://careers.example.com/jobs"
        self.session.commit()
        today = datetime.now(collectors.COLLECTION_TIMEZONE).date()
        listing_html = "".join(
            f'<a href="/jobs/role-{i}">A newly discovered engineering position number {i}</a>'
            for i in range(21)
        )
        listing = SimpleNamespace(status_code=200, text=listing_html)
        detail = SimpleNamespace(status_code=200, text="<html><body>No date published.</body></html>")
        with patch(
            "app.daily.fetchers.ats.httpx.get",
            side_effect=[listing] + [detail] * 20,
        ) as get:
            result = collectors.collect_target_events(self.session, self.target, today)

        ats = result["sources"]["ats"]
        self.assertEqual(get.call_count, 21)
        self.assertEqual(ats["undated_items"], 20)
        self.assertEqual(ats["not_checked_items"], 1)
        self.assertEqual(self.session.query(Event).filter_by(source_type="ats").count(), 20)
        overflow_url = "https://careers.example.com/jobs/role-20"
        self.assertIsNone(self.session.query(Event).filter_by(url=overflow_url).first())

    def test_past_date_keeps_source_dated_events_skips_snapshots_and_undated(self):
        self.target.ats_url = "https://careers.example.com/jobs"
        self.target.blog_url = "https://example.com/blog"
        self.target.web_social_url = "https://example.com/news"
        self.session.commit()
        past_date = date(2026, 11, 5)
        dated_job = {
            "title": "Dated role", "location": "Remote",
            "url": "https://careers.example.com/jobs/dated",
            "posted_at": "2026-11-05T08:00:00Z",
        }
        undated_job = {
            "title": "Undated role", "location": "Remote",
            "url": "https://careers.example.com/jobs/undated", "posted_at": None,
        }
        dated_post = {
            "title": "Dated post", "link": "https://example.com/blog/dated",
            "published": "2026-11-05T08:00:00Z",
        }
        undated_post = {
            "title": "Undated post", "link": "https://example.com/blog/undated",
            "published": None,
        }
        with (
            patch.object(collectors, "get_new_commits", return_value=[
                self.commit("past-commit", "2026-11-05T08:00:00Z")
            ]),
            patch.object(collectors, "get_ats_postings", return_value=[dated_job, undated_job]),
            patch.object(collectors, "get_blog_posts", return_value=[dated_post, undated_post]),
            patch.object(collectors, "get_web_social_content", side_effect=AssertionError("must not fetch historical snapshot")),
            patch.object(collectors, "_fetch_full_text", return_value="Details"),
        ):
            result = collectors.collect_target_events(self.session, self.target, past_date)

        self.assertEqual(result["sources"]["github"]["event_count"], 1)
        self.assertEqual(result["sources"]["ats"]["event_count"], 1)
        self.assertEqual(result["sources"]["ats"]["undated_items"], 1)
        self.assertEqual(result["sources"]["blog"]["event_count"], 1)
        self.assertEqual(result["sources"]["blog"]["undated_items"], 1)
        self.assertEqual(result["sources"]["web_social"]["status"], "skipped")
        self.assertIn("cannot be reconstructed", result["sources"]["web_social"]["highlight"])
        saved = self.session.query(Event).all()
        self.assertEqual({event.title for event in saved}, {"Commit in example/project", "Dated role", "Dated post"})
        self.assertIsNone(self.session.query(DailySourceCheckpoint).filter_by(
            target_id=self.target.id, source_type="ats",
        ).one().undated_items_baselined_at)

    def test_dated_ats_and_blog_items_outside_collection_day_are_ignored(self):
        self.target.github_url = None
        self.target.ats_url = "https://careers.example.com/jobs"
        self.target.blog_url = "https://example.com/blog"
        self.session.commit()
        collection_date = date(2026, 11, 6)
        with (
            patch.object(collectors, "get_ats_postings", return_value=[{
                "title": "Older role",
                "url": "https://careers.example.com/jobs/older",
                "posted_at": "2026-11-05T08:00:00Z",
            }]),
            patch.object(collectors, "get_blog_posts", return_value=[{
                "title": "Older post",
                "link": "https://example.com/blog/older",
                "published": "2026-11-05T08:00:00Z",
            }]),
        ):
            result = collectors.collect_target_events(
                self.session, self.target, collection_date
            )

        self.assertEqual(result["sources"]["ats"]["event_count"], 0)
        self.assertEqual(result["sources"]["ats"]["ignored_out_of_day"], 1)
        self.assertEqual(result["sources"]["blog"]["event_count"], 0)
        self.assertEqual(result["sources"]["blog"]["ignored_out_of_day"], 1)
        self.assertEqual(self.session.query(Event).count(), 0)

    def test_no_date_argument_keeps_current_date_undated_baseline_behavior(self):
        self.target.ats_url = "https://careers.example.com/jobs"
        self.session.commit()
        today = datetime.now(collectors.COLLECTION_TIMEZONE).date()
        with patch.object(collectors, "get_ats_postings", return_value=[{
            "title": "Existing role", "location": None,
            "url": "https://careers.example.com/jobs/existing", "posted_at": None,
        }]):
            result = collectors.collect_target_events(self.session, self.target)

        self.assertEqual(result["collection_date"], today)
        self.assertEqual(result["sources"]["ats"]["baseline_events"], 1)
        self.assertEqual(result["sources"]["ats"]["undated_items"], 1)


if __name__ == "__main__":
    unittest.main()
