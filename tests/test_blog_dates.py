import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.daily.fetchers.blogs import _extract_posts_from_html


FIXTURES = Path(__file__).parent / "fixtures"


class BlogDateTests(unittest.TestCase):
    def test_stripe_listing_dates_are_extracted_without_detail_requests(self):
        index_html = (FIXTURES / "stripe_blog_excerpt.html").read_text(encoding="utf-8")
        response = SimpleNamespace(status_code=200, text=index_html)

        with patch("app.daily.fetchers.blogs.httpx.get", return_value=response) as get:
            posts = _extract_posts_from_html("https://stripe.com/blog", include_content=False)

        self.assertEqual(get.call_count, 1)
        self.assertEqual(
            [post["published"] for post in posts],
            [
                "2026-11-05T00:00-08:00",
                "2026-10-30T00:00-07:00",
                "2026-10-27T00:00-07:00",
                "2026-10-22T00:00-07:00",
            ],
        )

    def test_detail_page_metadata_fills_date_when_listing_has_no_time_tag(self):
        index_html = """<article><a href="https://stripe.com/blog/example-article">
        A sufficiently long article title for the fallback scraper
        </a></article>"""
        article_html = (FIXTURES / "stripe_blog_article_excerpt.html").read_text(encoding="utf-8")
        responses = [
            SimpleNamespace(status_code=200, text=index_html),
            SimpleNamespace(status_code=200, text=article_html),
        ]

        with patch("app.daily.fetchers.blogs.httpx.get", side_effect=responses) as get:
            posts = _extract_posts_from_html(
                "https://stripe.com/blog", include_content=False, include_metadata=True,
            )

        self.assertEqual(get.call_count, 2)
        self.assertEqual(posts[0]["published"], "2026-11-05")
        self.assertEqual(posts[0]["date_state"], "dated")

    def test_successful_detail_without_date_is_undated_but_failure_is_not_checked(self):
        index_html = '<article><a href="https://example.com/blog/a-post-with-a-long-title">A sufficiently long article title</a></article>'
        no_date = SimpleNamespace(status_code=200, text="<html><body>No date.</body></html>")
        failed = [SimpleNamespace(status_code=200, text=index_html), no_date]
        with patch("app.daily.fetchers.blogs.httpx.get", side_effect=failed):
            posts = _extract_posts_from_html(
                "https://example.com/blog", include_content=False, include_metadata=True,
            )
        self.assertEqual(posts[0]["date_state"], "undated")

        with patch(
            "app.daily.fetchers.blogs.httpx.get",
            side_effect=[SimpleNamespace(status_code=200, text=index_html), httpx.ConnectError("offline")],
        ):
            posts = _extract_posts_from_html(
                "https://example.com/blog", include_content=False, include_metadata=True,
            )
        self.assertEqual(posts[0]["date_state"], "not_checked")

    def test_known_url_is_not_checked_and_does_not_fetch_detail(self):
        index_html = '<article><a href="https://example.com/blog/a-post-with-a-long-title">A sufficiently long article title</a></article>'
        with patch(
            "app.daily.fetchers.blogs.httpx.get",
            return_value=SimpleNamespace(status_code=200, text=index_html),
        ) as get:
            posts = _extract_posts_from_html(
                "https://example.com/blog", include_content=False, include_metadata=True,
                known_urls={"https://example.com/blog/a-post-with-a-long-title"},
            )
        self.assertEqual(posts[0]["date_state"], "not_checked")
        get.assert_called_once()

    def test_metadata_lookup_cap_marks_overflow_not_checked(self):
        links = "".join(
            f'<article><a href="https://example.com/blog/post-{i}">A sufficiently long title for post {i}</a></article>'
            for i in range(4)
        )
        listing = SimpleNamespace(status_code=200, text=links)
        detail = SimpleNamespace(status_code=200, text="<html><body>No date.</body></html>")
        with patch("app.daily.fetchers.blogs.httpx.get", side_effect=[listing, detail, detail, detail]) as get:
            posts = _extract_posts_from_html(
                "https://example.com/blog", include_content=False, include_metadata=True,
            )
        self.assertEqual(get.call_count, 4)
        self.assertEqual([post["date_state"] for post in posts], ["undated"] * 3 + ["not_checked"])


if __name__ == "__main__":
    unittest.main()
