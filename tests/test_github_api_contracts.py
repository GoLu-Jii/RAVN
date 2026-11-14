import unittest
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

from app.daily.fetchers.github import get_new_commits
from app.kundali import builder


def response(status=200, payload=None):
    result = Mock(status_code=status)
    result.json.return_value = payload
    result.raise_for_status.return_value = None
    return result


class DayZeroGitHubApiTests(unittest.TestCase):
    def test_account_type_uses_public_user_endpoint(self):
        with patch("app.kundali.builder.httpx.get", return_value=response(payload={"type": "Organization"})) as get:
            result = builder.get_account_type("example")

        self.assertEqual(result, "Organization")
        self.assertEqual(get.call_args.args[0], "https://api.github.com/users/example")
        self.assertEqual(get.call_args.kwargs["headers"]["Accept"], "application/vnd.github+json")

    def test_list_repos_uses_organization_endpoint_and_shapes_repositories(self):
        payload = [{
            "owner": {"login": "example"},
            "name": "project",
            "pushed_at": "2026-11-10T12:00:00Z",
        }]
        with (
            patch("app.kundali.builder.get_account_type", return_value="Organization"),
            patch("app.kundali.builder.httpx.get", return_value=response(payload=payload)) as get,
        ):
            result = builder.list_repos("https://github.com/example/", limit=3)

        self.assertEqual(result, [{
            "owner": "example",
            "repo": "project",
            "pushed_at": "2026-11-10T12:00:00Z",
        }])
        self.assertEqual(get.call_args.args[0], "https://api.github.com/orgs/example/repos")
        self.assertEqual(get.call_args.kwargs["params"], {"sort": "pushed", "per_page": 3})

    def test_user_repo_listing_uses_user_endpoint(self):
        with (
            patch("app.kundali.builder.get_account_type", return_value="User"),
            patch("app.kundali.builder.httpx.get", return_value=response(payload=[])) as get,
        ):
            self.assertEqual(builder.list_repos("github.com/octocat"), [])
        self.assertEqual(get.call_args.args[0], "https://api.github.com/users/octocat/repos")

    def test_commit_activity_retries_accepted_response_then_returns_data(self):
        payload = [{"total": 4, "week": 1}]
        with (
            patch(
                "app.kundali.builder.httpx.get",
                side_effect=[response(202), response(payload=payload)],
            ) as get,
            patch("app.kundali.builder.time.sleep") as sleep,
        ):
            result = builder.get_commit_activity("example", "project", max_retries=2)

        self.assertEqual(result, payload)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertEqual(get.call_args.args[0], "https://api.github.com/repos/example/project/stats/commit_activity")

    def test_commit_activity_fails_fast_on_non_200_non_202(self):
        with (
            patch("app.kundali.builder.httpx.get", return_value=response(404)) as get,
            patch("app.kundali.builder.time.sleep") as sleep,
        ):
            self.assertIsNone(builder.get_commit_activity("example", "missing"))
        get.assert_called_once()
        sleep.assert_not_called()

    def test_recent_commits_sends_since_and_normalizes_response(self):
        payload = [{
            "sha": "abc",
            "commit": {
                "message": "Improve search",
                "author": {"date": "2026-11-10T10:00:00Z"},
            },
        }]
        with patch("app.kundali.builder.httpx.get", return_value=response(payload=payload)) as get:
            result = builder.get_recent_commits("example", "project", days=7, limit=5)

        self.assertEqual(result, [{
            "sha": "abc",
            "message": "Improve search",
            "date": "2026-11-10T10:00:00Z",
        }])
        self.assertEqual(get.call_args.args[0], "https://api.github.com/repos/example/project/commits")
        self.assertEqual(get.call_args.kwargs["params"]["per_page"], 5)
        self.assertTrue(get.call_args.kwargs["params"]["since"].endswith("Z"))

    def test_languages_returns_language_byte_counts(self):
        payload = {"Python": 1200, "TypeScript": 800}
        with patch("app.kundali.builder.httpx.get", return_value=response(payload=payload)) as get:
            self.assertEqual(builder.get_languages("example", "project"), payload)
        self.assertEqual(get.call_args.args[0], "https://api.github.com/repos/example/project/languages")


class DailyGitHubApiTests(unittest.TestCase):
    def test_daily_fetch_paginates_repo_and_commit_endpoints_and_shapes_events(self):
        repos = response(payload=[{
            "pushed_at": "2026-11-10T12:00:00Z",
            "owner": {"login": "example"},
            "name": "project",
        }])
        commits = response(payload=[{
            "sha": "abc",
            "commit": {
                "message": "Daily change",
                "committer": {"date": "2026-11-10T11:00:00Z"},
                "author": {"date": "2026-11-10T10:00:00Z"},
            },
            "html_url": "https://github.com/example/project/commit/abc",
        }])
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.side_effect = [repos, commits]

        with (
            patch("app.daily.fetchers.github.get_account_type", return_value="Organization"),
            patch("app.daily.fetchers.github.httpx.Client", return_value=client) as client_factory,
        ):
            result = get_new_commits("https://github.com/example", datetime(2026, 11, 10))

        self.assertEqual(result, [{
            "sha": "abc",
            "repo": "example/project",
            "message": "Daily change",
            "date": "2026-11-10T11:00:00Z",
            "url": "https://github.com/example/project/commit/abc",
        }])
        client_factory.assert_called_once()
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(client.get.call_args_list[0].args[0], "https://api.github.com/orgs/example/repos")
        self.assertEqual(client.get.call_args_list[1].args[0], "https://api.github.com/repos/example/project/commits")

    def test_daily_fetch_ignores_repositories_without_commits(self):
        repos = response(payload=[{
            "pushed_at": "2026-11-10T12:00:00Z",
            "owner": {"login": "example"},
            "name": "empty",
        }])
        no_commits = response(409, payload={})
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.get.side_effect = [repos, no_commits]
        with (
            patch("app.daily.fetchers.github.get_account_type", return_value="Organization"),
            patch("app.daily.fetchers.github.httpx.Client", return_value=client),
        ):
            result = get_new_commits("https://github.com/example", datetime(2026, 11, 10))
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
