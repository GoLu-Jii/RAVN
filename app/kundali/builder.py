import os
import httpx
import time
from urllib.parse import urlparse
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_API_BASE = "https://api.github.com"


def _get_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def extract_account_name(github_url: str) -> str:

    path = urlparse(github_url).path 
    return path.strip("/").split("/")[0]



def get_account_type(name: str) -> str:

    response = httpx.get(
        f"{GITHUB_API_BASE}/users/{name}",
        headers=_get_headers(),
    )
    response.raise_for_status()
    return response.json()["type"]


def list_repos(github_url: str, limit: int = 10) -> list[dict]:


    name = extract_account_name(github_url)
    account_type = get_account_type(name)

    if account_type == "Organization":
        url = f"{GITHUB_API_BASE}/orgs/{name}/repos"
    elif account_type == "User":
        url = f"{GITHUB_API_BASE}/users/{name}/repos"
    else:
        raise ValueError(f"Unexpected account type '{account_type}' for {name}")

    params = {
        "sort": "pushed",
        "per_page": limit,
    }

    response = httpx.get(url, headers=_get_headers(), params=params)
    response.raise_for_status()

    repos = response.json()

    shaped = []
    for repo in repos:
        shaped.append({
            "owner": repo["owner"]["login"],
            "repo": repo["name"],
            "pushed_at": repo["pushed_at"],
        })

    return shaped




def get_commit_activity(owner: str, repo: str, max_retries: int = 5, wait_seconds: int = 2) -> list[dict] | None:
    """
    Fetch 52 weeks of commit activity for a repo.

    Only retries on 202 (stats still computing). Any other non-200 status
    (404, 403, 500, etc.) fails immediately - retrying those wastes time
    since they won't resolve by waiting.

    Returns None if the repo never resolved out of 202 (e.g. zero-commit
    repos never get cached) or if any other error occurred - logged
    separately so the two cases are distinguishable.
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/stats/commit_activity"

    for attempt in range(max_retries):
        try:
            response = httpx.get(url, headers=_get_headers())
        except httpx.RequestError as e:
            print(f"[get_commit_activity] network error for {owner}/{repo}: {e}")
            return None

        if response.status_code == 202:
            print(f"[get_commit_activity] {owner}/{repo} still computing, attempt {attempt + 1}/{max_retries}")
            time.sleep(wait_seconds)
            continue

        if response.status_code == 200:
            data = response.json()
            return data if data else []

        # Any other status (404, 403, 500, ...) - don't retry, fail now
        print(f"[get_commit_activity] {owner}/{repo} returned {response.status_code}, giving up")
        return None

    # Exhausted retries, still stuck on 202
    print(f"[get_commit_activity] {owner}/{repo} never resolved out of 202 after {max_retries} attempts")
    return None





from datetime import datetime, timedelta, timezone


def get_recent_commits(owner: str, repo: str, days: int = 45, limit: int = 50) -> list[dict]:
    """
    Fetch commit messages from the last `days` days for a repo.
    Uses GitHub's `since` param so filtering happens server-side.

    Returns a list of dicts:
        [{"sha": "...", "message": "...", "date": "..."}, ...]
    """
    since_date = datetime.now(timezone.utc) - timedelta(days=days)
    since_str = since_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits"

    params = {
        "since": since_str,
        "per_page": limit,
    }

    response = httpx.get(url, headers=_get_headers(), params=params)
    response.raise_for_status()

    commits = response.json()

    shaped = []
    for commit in commits:
        shaped.append({
            "sha": commit["sha"],
            "message": commit["commit"]["message"],
            "date": commit["commit"]["author"]["date"],
        })

    return shaped





def get_languages(owner: str, repo: str) -> dict:
    """
    Fetch the language breakdown for a repo.

    Returns a dict of {language: byte_count}, e.g.
        {"TypeScript": 1234567, "JavaScript": 45678, "CSS": 3210}
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/languages"

    response = httpx.get(url, headers=_get_headers())
    response.raise_for_status()

    return response.json()