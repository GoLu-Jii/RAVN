import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.daily.fetchers import ats


def response(payload, status=200):
    return SimpleNamespace(
        status_code=status,
        json=lambda: payload,
        raise_for_status=lambda: None,
    )


class ATSProviderApiTests(unittest.TestCase):
    def test_greenhouse_api_shapes_jobs_and_requests_board(self):
        with patch("app.daily.fetchers.ats.httpx.get", return_value=response({
            "jobs": [{
                "id": 42,
                "title": "Engineer",
                "location": {"name": "Remote"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/42",
                "first_published": "2026-11-10T12:00:00Z",
            }],
        })) as get:
            result = ats.fetch_greenhouse_jobs("https://boards.greenhouse.io/acme")

        self.assertEqual(result[0]["title"], "Engineer")
        self.assertEqual(result[0]["location"], "Remote")
        self.assertEqual(result[0]["posted_at"], "2026-11-10T12:00:00Z")
        self.assertEqual(result[0]["date_state"], "dated")
        self.assertEqual(get.call_args.args[0], "https://boards-api.greenhouse.io/v1/boards/acme/jobs")

    def test_greenhouse_detail_api_reads_first_published(self):
        with patch("app.daily.fetchers.ats.httpx.get", return_value=response({
            "first_published": "2026-11-10",
        })) as get:
            result = ats._greenhouse_job_date("acme", 42)
        self.assertEqual(result, ("2026-11-10", "dated"))
        self.assertEqual(get.call_args.args[0], "https://boards-api.greenhouse.io/v1/boards/acme/jobs/42")

    def test_lever_api_maps_created_at_and_hosted_url(self):
        with patch("app.daily.fetchers.ats.httpx.get", return_value=response([{
            "text": "Engineer",
            "categories": {"location": "Remote"},
            "hostedUrl": "https://jobs.lever.co/acme/42",
            "createdAt": 1794312000000,
        }])) as get:
            result = ats.fetch_lever_jobs("https://jobs.lever.co/acme")
        self.assertEqual(result[0]["title"], "Engineer")
        self.assertEqual(result[0]["location"], "Remote")
        self.assertEqual(result[0]["url"], "https://jobs.lever.co/acme/42")
        self.assertEqual(result[0]["posted_at"], 1794312000000)
        self.assertEqual(get.call_args.args[0], "https://api.lever.co/v0/postings/acme?mode=json")

    def test_ashby_api_maps_public_board_postings(self):
        with patch("app.daily.fetchers.ats.httpx.get", return_value=response({"jobs": [{
            "title": "Designer",
            "location": "New York",
            "jobUrl": "https://jobs.ashbyhq.com/acme/42",
            "publishedAt": "2026-11-10T12:00:00Z",
        }]})) as get:
            result = ats.fetch_ashbyhq_jobs("https://jobs.ashbyhq.com/acme")
        self.assertEqual(result[0]["title"], "Designer")
        self.assertEqual(result[0]["url"], "https://jobs.ashbyhq.com/acme/42")
        self.assertEqual(result[0]["posted_at"], "2026-11-10T12:00:00Z")
        self.assertEqual(get.call_args.args[0], "https://api.ashbyhq.com/posting-api/job-board/acme")

    def test_workable_api_maps_postings(self):
        with patch("app.daily.fetchers.ats.httpx.get", return_value=response({"jobs": [{
            "title": "Analyst",
            "location": "Remote",
            "url": "https://apply.workable.com/acme/j/42",
            "published_on": "2026-11-10",
        }]})) as get:
            result = ats.fetch_workable_jobs("https://apply.workable.com/acme")
        self.assertEqual(result[0]["title"], "Analyst")
        self.assertEqual(result[0]["url"], "https://apply.workable.com/acme/j/42")
        self.assertEqual(result[0]["posted_at"], "2026-11-10")
        self.assertEqual(get.call_args.args[0], "https://www.workable.com/api/accounts/acme?details=true")

    def test_dispatch_selects_provider_and_normalizes_date_state(self):
        with patch(
            "app.daily.fetchers.ats.fetch_lever_jobs",
            return_value=[{
                "title": "Engineer",
                "url": "https://jobs.lever.co/acme/42",
                "posted_at": "2026-11-10",
            }],
        ) as fetch:
            result = ats.get_ats_postings("https://jobs.lever.co/acme")
        fetch.assert_called_once()
        self.assertEqual(result[0]["date_state"], "dated")

    def test_greenhouse_current_day_prefilter_and_known_urls_avoid_detail_fetch(self):
        listing = response({"jobs": [
            {
                "id": 1, "title": "Known", "updated_at": "2026-11-10T12:00:00Z",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
            },
            {
                "id": 2, "title": "Other day", "updated_at": "2026-11-09T12:00:00Z",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
            },
        ]})
        with (
            patch("app.daily.fetchers.ats.httpx.get", return_value=listing) as get,
            patch("app.daily.fetchers.ats._greenhouse_job_date") as detail,
        ):
            result = ats.fetch_greenhouse_jobs(
                "https://boards.greenhouse.io/acme",
                collection_date=date(2026, 11, 10),
                known_urls={"https://boards.greenhouse.io/acme/jobs/1"},
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date_check_reason"], "known")
        get.assert_called_once()
        detail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
