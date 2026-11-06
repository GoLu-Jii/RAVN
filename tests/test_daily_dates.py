import time
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.daily.dates import _parse_date
from app.daily.fetchers.blogs import get_blog_posts


IST = ZoneInfo("Asia/Kolkata")


class ParseSourceDateTests(unittest.TestCase):
    def ist_date(self, value):
        parsed = _parse_date(value)
        self.assertIsNotNone(parsed)
        return parsed.astimezone(IST).date()

    def test_aware_ist_datetimes_keep_their_local_date(self):
        self.assertEqual(self.ist_date(datetime(2026, 11, 5, 23, 59, tzinfo=IST)), date(2026, 11, 5))
        self.assertEqual(self.ist_date(datetime(2026, 11, 6, 0, 1, tzinfo=IST)), date(2026, 11, 6))

    def test_utc_timestamp_crosses_into_next_ist_day(self):
        self.assertEqual(self.ist_date("2026-11-05T20:00:00Z"), date(2026, 11, 6))

    def test_date_only_value_keeps_its_calendar_date(self):
        self.assertEqual(self.ist_date("2026-11-05"), date(2026, 11, 5))
        self.assertEqual(self.ist_date(date(2026, 11, 5)), date(2026, 11, 5))

    def test_iso_z_suffix_is_parsed(self):
        parsed = _parse_date("2026-11-05T20:00:00Z")
        self.assertEqual(parsed, datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc))

    def test_epoch_milliseconds_are_parsed(self):
        epoch_ms = int(datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(
            _parse_date(epoch_ms),
            datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc),
        )

    def test_feedparser_struct_time_is_treated_as_utc(self):
        parsed_tuple = time.strptime("2026-11-05 20:00:00", "%Y-%m-%d %H:%M:%S")
        self.assertEqual(self.ist_date(parsed_tuple), date(2026, 11, 6))

    def test_naive_datetime_is_treated_as_utc(self):
        self.assertEqual(
            _parse_date(datetime(2026, 11, 5, 20, 0)),
            datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc),
        )

    def test_unparseable_value_returns_none(self):
        self.assertIsNone(_parse_date("not a date"))


class BlogFeedDateFallbackTests(unittest.TestCase):
    def test_published_date_fields_are_used_in_order(self):
        cases = [
            ({"published": "pub", "published_parsed": "pub-parsed", "updated": "updated"}, "pub"),
            ({"published_parsed": "pub-parsed", "updated": "updated"}, "pub-parsed"),
            ({"updated": "updated", "updated_parsed": "updated-parsed"}, "updated"),
            ({"updated_parsed": "updated-parsed"}, "updated-parsed"),
        ]

        for entry_fields, expected in cases:
            entry = {"title": "A sufficiently long blog post title", "link": "https://example.com/post"}
            entry.update(entry_fields)
            with self.subTest(expected=expected):
                with (
                    patch("app.daily.fetchers.blogs.discover_feed_url", return_value="https://example.com/feed"),
                    patch(
                        "app.daily.fetchers.blogs.httpx.get",
                        return_value=SimpleNamespace(status_code=200, content=b"feed"),
                    ),
                    patch(
                        "app.daily.fetchers.blogs.feedparser.parse",
                        return_value=SimpleNamespace(entries=[entry]),
                    ),
                ):
                    posts = get_blog_posts("https://example.com/blog", include_content=False)
                self.assertEqual(posts[0]["published"], expected)


if __name__ == "__main__":
    unittest.main()
