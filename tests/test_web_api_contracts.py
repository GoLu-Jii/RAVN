import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.daily.fetchers import blogs, socials
from app.daily import collectors
from app.targets import onboarding


def page(status=200, text="", headers=None):
    return SimpleNamespace(
        status_code=status,
        text=text,
        content=text.encode("utf-8"),
        headers=headers or {},
        raise_for_status=lambda: None,
    )


class OnboardingWebsiteTests(unittest.TestCase):
    def test_homepage_fetch_follows_redirects_and_resolves_links(self):
        with patch(
            "app.targets.onboarding.httpx.get",
            return_value=page(text='<a href="/careers">Jobs</a>'),
        ) as get:
            result = onboarding.extract_links("example.com")

        self.assertEqual(result, [("https://example.com/careers", "Jobs")])
        self.assertEqual(get.call_args.args[0], "https://example.com")
        self.assertTrue(get.call_args.kwargs["follow_redirects"])

    def test_homepage_transport_and_http_failures_return_no_candidates(self):
        with patch(
            "app.targets.onboarding.httpx.get",
            side_effect=httpx.ConnectError("offline"),
        ):
            self.assertEqual(onboarding.extract_links("https://example.com"), [])
        with patch(
            "app.targets.onboarding.httpx.get",
            return_value=page(status=503),
        ):
            self.assertEqual(onboarding.extract_links("https://example.com"), [])


class BlogWebsiteTests(unittest.TestCase):
    def test_feed_discovery_accepts_xml_url(self):
        with patch(
            "app.daily.fetchers.blogs.httpx.get",
            return_value=page(headers={"content-type": "application/rss+xml"}),
        ) as get:
            result = blogs.discover_feed_url("https://example.com/feed")
        self.assertEqual(result, "https://example.com/feed")
        get.assert_called_once()

    def test_feed_discovery_reads_alternate_link_from_html(self):
        html = '<html><head><link rel="alternate" type="application/rss+xml" href="/news/feed.xml"></head></html>'
        with patch(
            "app.daily.fetchers.blogs.httpx.get",
            return_value=page(text=html, headers={"content-type": "text/html"}),
        ):
            self.assertEqual(
                blogs.discover_feed_url("https://example.com/news"),
                "https://example.com/news/feed.xml",
            )

    def test_feed_api_parses_entries_without_fetching_article_when_not_requested(self):
        entry = {
            "title": "Product update",
            "link": "https://example.com/blog/update",
            "published": "2026-11-10",
        }
        with (
            patch("app.daily.fetchers.blogs.discover_feed_url", return_value="https://example.com/feed.xml"),
            patch("app.daily.fetchers.blogs.httpx.get", return_value=page()) as get,
            patch("app.daily.fetchers.blogs.feedparser.parse", return_value=SimpleNamespace(entries=[entry])),
        ):
            result = blogs.get_blog_posts("https://example.com/blog", include_content=False)
        self.assertEqual(result[0]["title"], "Product update")
        self.assertEqual(result[0]["published"], "2026-11-10")
        self.assertEqual(result[0]["date_state"], "dated")
        get.assert_called_once_with(
            "https://example.com/feed.xml",
            headers=blogs._HEADERS,
            timeout=10,
            follow_redirects=True,
        )

    def test_feed_article_fetch_extracts_content(self):
        entry = {"title": "Product update", "link": "https://example.com/blog/update"}
        with (
            patch("app.daily.fetchers.blogs.discover_feed_url", return_value="https://example.com/feed.xml"),
            patch("app.daily.fetchers.blogs.httpx.get", side_effect=[page(), page(text="<article>body</article>")]) as get,
            patch("app.daily.fetchers.blogs.feedparser.parse", return_value=SimpleNamespace(entries=[entry])),
            patch("app.daily.fetchers.blogs.trafilatura.extract", return_value="Article text"),
        ):
            result = blogs.get_blog_posts("https://example.com/blog", include_content=True)
        self.assertEqual(result[0]["content"], "Article text")
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[1].args[0], "https://example.com/blog/update")

    def test_unavailable_feed_falls_back_to_html_listing(self):
        with (
            patch("app.daily.fetchers.blogs.discover_feed_url", return_value=None),
            patch(
                "app.daily.fetchers.blogs.httpx.get",
                return_value=page(text='<a href="/blog/a-post">A sufficiently long title for a blog post</a>'),
            ) as get,
        ):
            result = blogs.get_blog_posts("https://example.com", include_content=False)
        self.assertEqual(result[0]["link"], "https://example.com/blog/a-post")
        get.assert_called_once()


class PublicWebAndSocialTests(unittest.TestCase):
    def test_social_page_extracts_long_public_text(self):
        with (
            patch(
                "app.daily.fetchers.socials.httpx.get",
                return_value=page(text="<html>public page</html>"),
            ) as get,
            patch("app.daily.fetchers.socials.trafilatura.extract", return_value="A" * 60),
        ):
            result = socials.get_web_social_content("https://example.com/news")
        self.assertEqual(result, {"url": "https://example.com/news", "content": "A" * 60})
        self.assertTrue(get.call_args.kwargs["follow_redirects"])

    def test_social_page_is_none_for_short_or_non_success_content(self):
        with (
            patch("app.daily.fetchers.socials.httpx.get", return_value=page()),
            patch("app.daily.fetchers.socials.trafilatura.extract", return_value="short"),
        ):
            self.assertIsNone(socials.get_web_social_content("https://example.com"))
        with patch("app.daily.fetchers.socials.httpx.get", return_value=page(status=502)):
            self.assertIsNone(socials.get_web_social_content("https://example.com"))

    def test_collector_long_form_fetch_extracts_article_text(self):
        with (
            patch(
                "app.daily.collectors.httpx.get",
                return_value=page(text="<article>full text</article>"),
            ) as get,
            patch("app.daily.collectors.trafilatura.extract", return_value="Full article text"),
        ):
            result = collectors._fetch_full_text("https://example.com/article", fallback="title")
        self.assertEqual(result, "Full article text")
        self.assertTrue(get.call_args.kwargs["follow_redirects"])

    def test_collector_long_form_fetch_uses_fallback_when_unavailable(self):
        with patch(
            "app.daily.collectors.httpx.get",
            side_effect=httpx.ConnectError("offline"),
        ):
            self.assertEqual(
                collectors._fetch_full_text("https://example.com/article", fallback="title"),
                "title",
            )


if __name__ == "__main__":
    unittest.main()
