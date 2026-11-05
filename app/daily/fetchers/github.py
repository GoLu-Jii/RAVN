from datetime import datetime, timezone

import httpx

from app.kundali.builder import GITHUB_API_BASE, _get_headers, extract_account_name, get_account_type


def get_new_commits(github_url: str, since: datetime) -> list[dict]:
    """Fetch commits from the supplied daily window start onward."""
    if not github_url:
        return []

    account = extract_account_name(github_url)
    account_type = get_account_type(account)
    if account_type == "Organization":
        repos_url = f"{GITHUB_API_BASE}/orgs/{account}/repos"
    elif account_type == "User":
        repos_url = f"{GITHUB_API_BASE}/users/{account}/repos"
    else:
        raise ValueError(f"Unexpected GitHub account type '{account_type}' for {account}")

    since = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since.astimezone(timezone.utc)
    cutoff = since
    repositories = []
    new_commits = []
    # The collector can make many requests for a large organization. Reuse the
    # GitHub connection instead of opening a new connection for every page.
    with httpx.Client(headers=_get_headers(), timeout=15) as client:
        page = 1
        while True:
            repos_response = client.get(
                repos_url,
                params={"type": "public", "sort": "full_name", "per_page": 100, "page": page},
            )
            repos_response.raise_for_status()
            repos = repos_response.json()
            repositories.extend(repos)
            if len(repos) < 100:
                break
            page += 1

        # The collector passes the start of today's collection window; do not
        # include commits from earlier dates.
        since_value = cutoff.isoformat().replace("+00:00", "Z")
        for repo in repositories:
            pushed_at = repo.get("pushed_at")
            if not pushed_at:
                continue
            pushed_date = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            if pushed_date < cutoff:
                continue
            owner = repo["owner"]["login"]
            name = repo["name"]
            page = 1
            while True:
                response = client.get(
                    f"{GITHUB_API_BASE}/repos/{owner}/{name}/commits",
                    params={"since": since_value, "per_page": 100, "page": page},
                )
                if response.status_code == 409:
                    # GitHub returns 409 for repositories with no commits yet.
                    break
                response.raise_for_status()
                items = response.json()
                for item in items:
                    commit = item.get("commit", {})
                    committer = commit.get("committer") or {}
                    author = commit.get("author") or {}
                    new_commits.append({
                        "sha": item.get("sha"),
                        "repo": f"{owner}/{name}",
                        "message": commit.get("message", ""),
                        "date": committer.get("date") or author.get("date"),
                        "url": item.get("html_url"),
                    })
                if len(items) < 100:
                    break
                page += 1
    return new_commits
