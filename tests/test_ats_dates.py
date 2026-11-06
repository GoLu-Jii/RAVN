import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.daily.fetchers.ats import (
    _extract_jobs_from_html,
    _job_page_date,
    _jsonld_date_posted,
    fetch_greenhouse_jobs,
)


class JobJsonLdDateTests(unittest.TestCase):
    def test_reads_jobposting_date_in_list_and_graph(self):
        payload = [{
            "@type": "Organization",
            "@graph": [
                {"@type": ["Thing", "JobPosting"], "datePosted": "2026-11-05"},
            ],
        }]
        self.assertEqual(_jsonld_date_posted(payload), "2026-11-05")

    def test_returns_none_for_no_jobposting_or_missing_date(self):
        self.assertIsNone(_jsonld_date_posted({"@type": "Organization", "datePosted": "2026-11-05"}))
        self.assertIsNone(_jsonld_date_posted({"@type": "JobPosting"}))

    def test_fallback_does_not_refetch_known_listing_urls(self):
        page = SimpleNamespace(
            status_code=200,
            text='<a href="/jobs/role">A sufficiently descriptive role title</a>',
        )
        with patch("app.daily.fetchers.ats.httpx.get", return_value=page) as get:
            jobs = _extract_jobs_from_html(
                "https://example.com/careers",
                known_urls={"https://example.com/jobs/role"},
            )
        self.assertEqual(len(jobs), 1)
        self.assertIsNone(jobs[0]["posted_at"])
        self.assertEqual(jobs[0]["date_state"], "not_checked")
        get.assert_called_once()

    def test_fallback_reads_dateposted_from_unseen_detail_page(self):
        listing = SimpleNamespace(
            status_code=200,
            text='<a href="/jobs/role">A sufficiently descriptive role title</a>',
        )
        detail = SimpleNamespace(
            status_code=200,
            text='<script type="application/ld+json">{"@type":"JobPosting","datePosted":"2026-11-05T10:00:00Z"}</script>',
        )
        with patch("app.daily.fetchers.ats.httpx.get", side_effect=[listing, detail]) as get:
            jobs = _extract_jobs_from_html("https://example.com/careers")
        self.assertEqual(jobs[0]["posted_at"], "2026-11-05T10:00:00Z")
        self.assertEqual(jobs[0]["date_state"], "dated")
        self.assertEqual(get.call_count, 2)

    def test_successful_detail_page_without_date_is_genuinely_undated(self):
        listing = SimpleNamespace(
            status_code=200,
            text='<a href="/jobs/role">A sufficiently descriptive role title</a>',
        )
        detail = SimpleNamespace(status_code=200, text="<html><body>No structured date.</body></html>")
        with patch("app.daily.fetchers.ats.httpx.get", side_effect=[listing, detail]):
            jobs = _extract_jobs_from_html("https://example.com/careers")
        self.assertEqual(jobs[0]["date_state"], "undated")

    def test_failed_detail_page_is_not_checked(self):
        listing = SimpleNamespace(
            status_code=200,
            text='<a href="/jobs/role">A sufficiently descriptive role title</a>',
        )
        with patch(
            "app.daily.fetchers.ats.httpx.get",
            side_effect=[listing, httpx.ConnectError("offline")],
        ):
            jobs = _extract_jobs_from_html("https://example.com/careers")
        self.assertEqual(jobs[0]["date_state"], "not_checked")

    def test_detail_page_skips_malformed_json_and_reads_later_graph(self):
        fixture = Path(__file__).parent / "fixtures" / "ats_job_malformed_then_valid.html"
        response = SimpleNamespace(status_code=200, text=fixture.read_text(encoding="utf-8"))
        with patch("app.daily.fetchers.ats.httpx.get", return_value=response):
            self.assertEqual(_job_page_date("https://example.com/jobs/role"), "2026-11-05")


class GreenhouseSourceDateTests(unittest.TestCase):
    def test_updated_at_prefilters_and_first_published_determines_date(self):
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {"jobs": [
                {"id": 1, "title": "Candidate", "updated_at": "2026-11-05T12:00:00Z", "absolute_url": "https://boards.greenhouse.io/example/jobs/1"},
                {"id": 2, "title": "Other day", "updated_at": "2026-11-04T12:00:00Z", "absolute_url": "https://boards.greenhouse.io/example/jobs/2"},
            ]},
        )
        with (
            patch("app.daily.fetchers.ats.httpx.get", return_value=response),
            patch("app.daily.fetchers.ats._greenhouse_job_date", return_value=("2026-10-01T10:00:00Z", "dated")) as detail,
        ):
            jobs = fetch_greenhouse_jobs(
                "https://boards.greenhouse.io/example",
                collection_date=date(2026, 11, 5),
            )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["posted_at"], "2026-10-01T10:00:00Z")
        detail.assert_called_once_with("example", 1, strict=False)

    def test_detail_page_lookups_are_capped(self):
        jobs_payload = [{
            "id": index,
            "title": f"Role {index}",
            "updated_at": "2026-11-05T12:00:00Z",
            "absolute_url": f"https://boards.greenhouse.io/example/jobs/{index}",
        } for index in range(21)]
        response = SimpleNamespace(status_code=200, json=lambda: {"jobs": jobs_payload})
        with (
            patch("app.daily.fetchers.ats.httpx.get", return_value=response),
            patch("app.daily.fetchers.ats._greenhouse_job_date", return_value=("2026-11-05T12:00:00Z", "dated")) as detail,
        ):
            jobs = fetch_greenhouse_jobs(
                "https://boards.greenhouse.io/example",
                collection_date=date(2026, 11, 5),
            )
        self.assertEqual(len(jobs), 21)
        self.assertEqual(detail.call_count, 20)
        self.assertTrue(all(job["date_state"] == "dated" for job in jobs[:20]))
        self.assertEqual(jobs[-1]["date_state"], "not_checked")
        self.assertIsNone(jobs[-1]["posted_at"])


if __name__ == "__main__":
    unittest.main()
