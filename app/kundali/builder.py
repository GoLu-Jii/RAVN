import os
import httpx
from urllib.parse import urlparse
from dotenv import load_dotenv

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