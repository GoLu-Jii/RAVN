def detect_ats_platform(ats_url: str) -> str | None:
    if "greenhouse.io" in ats_url:
        return "Greenhouse"
    elif "lever.co" in ats_url:
        return "lever"
    elif "ashbyhq.com" in ats_url:
        return "ashbyhq"
    elif "workable.com" in ats_url:
        return "workable"
    else:
        return None


import httpx


def _extract_board_token(ats_url: str) -> str | None:
    """
    Pulls the company/board token out of the URL's last path segment.
    e.g. https://boards.greenhouse.io/stripe -> "stripe"
    """
    path = ats_url.rstrip("/").split("/")
    if not path or not path[-1]:
        return None
    return path[-1]


def fetch_greenhouse_jobs(ats_url: str) -> list[dict]:
    token = _extract_board_token(ats_url)
    if not token:
        return []

    api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

    try:
        response = httpx.get(api_url, timeout=10)
    except httpx.RequestError as e:
        print(f"[fetch_greenhouse_jobs] network error for {token}: {e}")
        return []

    if response.status_code != 200:
        print(f"[fetch_greenhouse_jobs] {token} returned {response.status_code}")
        return []

    jobs = response.json().get("jobs", [])

    return [
        {
            "title": job.get("title"),
            "location": (job.get("location") or {}).get("name"),
            "url": job.get("absolute_url"),
            "posted_at": job.get("updated_at"),
        }
        for job in jobs
    ]


def fetch_lever_jobs(ats_url: str) -> list[dict]:
    token = _extract_board_token(ats_url)
    if not token:
        return []

    api_url = f"https://api.lever.co/v0/postings/{token}?mode=json"

    try:
        response = httpx.get(api_url, timeout=10)
    except httpx.RequestError as e:
        print(f"[fetch_lever_jobs] network error for {token}: {e}")
        return []

    if response.status_code != 200:
        print(f"[fetch_lever_jobs] {token} returned {response.status_code}")
        return []

    jobs = response.json()

    return [
        {
            "title": job.get("text"),
            "location": (job.get("categories") or {}).get("location"),
            "url": job.get("hostedUrl"),
            "posted_at": job.get("createdAt"),  # Unix ms timestamp, not ISO string
        }
        for job in jobs
    ]


def fetch_ashbyhq_jobs(ats_url: str) -> list[dict]:
    """
    UNVERIFIED - Ashby's public posting API is less standardized than
    Greenhouse/Lever's. This assumes the common documented shape
    (org-name based endpoint returning a "jobs" list) - confirm against
    a real Ashby board before trusting this.
    """
    token = _extract_board_token(ats_url)
    if not token:
        return []

    api_url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"

    try:
        response = httpx.get(api_url, timeout=10)
    except httpx.RequestError as e:
        print(f"[fetch_ashbyhq_jobs] network error for {token}: {e}")
        return []

    if response.status_code != 200:
        print(f"[fetch_ashbyhq_jobs] {token} returned {response.status_code}")
        return []

    jobs = response.json().get("jobs", [])

    return [
        {
            "title": job.get("title"),
            "location": job.get("location"),
            "url": job.get("jobUrl"),
            "posted_at": job.get("publishedAt"),
        }
        for job in jobs
    ]


def fetch_workable_jobs(ats_url: str) -> list[dict]:
    token = _extract_board_token(ats_url)
    if not token:
        return []

    api_url = f"https://www.workable.com/api/accounts/{token}?details=true"

    try:
        response = httpx.get(api_url, timeout=10)
    except httpx.RequestError as e:
        print(f"[fetch_workable_jobs] network error for {token}: {e}")
        return []

    if response.status_code != 200:
        print(f"[fetch_workable_jobs] {token} returned {response.status_code}")
        return []

    jobs = response.json().get("jobs", [])

    return [
        {
            "title": job.get("title"),
            "location": job.get("location"),
            "url": job.get("url") or job.get("shortlink"),
            "posted_at": job.get("published_on") or job.get("created_at"),
        }
        for job in jobs
    ]


def get_ats_postings(ats_url: str) -> list[dict]:
    platform = detect_ats_platform(ats_url)
    
    if platform == "Greenhouse":
        metadata = fetch_greenhouse_jobs(ats_url)
    elif platform == "lever":
        metadata = fetch_lever_jobs(ats_url)
    elif platform == "ashbyhq":
        metadata = fetch_ashbyhq_jobs(ats_url)
    elif platform == "workable":
        metadata = fetch_workable_jobs(ats_url)
    else:
        metadata = []

    return metadata