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


def _extract_jobs_from_html(ats_url: str, max_jobs: int = 30) -> list[dict]:
    """
    Fallback extractor for company-hosted careers pages (e.g. stripe.com/careers).
    Probes the page and common subpaths for job listing links or embedded ATS boards.
    """
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    urls_to_try = [ats_url]
    base_url = ats_url.rstrip("/")
    for sub in ["/search", "/open-roles", "/jobs", "/all-roles"]:
        if not base_url.endswith(sub):
            urls_to_try.append(base_url + sub)

    jobs = []
    seen = set()

    for url in urls_to_try:
        try:
            r = httpx.get(url, headers=headers, timeout=8, follow_redirects=True)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")

            # 1. Check if page references an external ATS (Greenhouse, Lever, etc.)
            for a in soup.find_all("a"):
                href = a.get("href", "")
                ext_platform = detect_ats_platform(href)
                if ext_platform:
                    ext_jobs = get_ats_postings(href)
                    if ext_jobs:
                        return ext_jobs[:max_jobs]

            # 2. Extract job links matching common career posting patterns
            for a in soup.find_all("a"):
                href = a.get("href", "")
                if not href or href.startswith("#") or href.startswith("mailto:"):
                    continue

                is_job_link = any(k in href.lower() for k in ["/listing/", "/careers/listing/", "/jobs/", "/job/", "/position/", "/positions/", "/openings/"])
                if not is_job_link:
                    continue

                text = a.get_text(" ", strip=True)
                if len(text) < 6 or any(k in text.lower() for k in ["privacy", "terms", "cookie", "view all", "open roles", "careers home", "emerging talent"]):
                    continue

                if text not in seen:
                    seen.add(text)
                    jobs.append({
                        "title": text,
                        "location": None,
                        "url": urljoin(url, href),
                        "posted_at": None,
                    })

                if len(jobs) >= max_jobs:
                    break
        except Exception as e:
            print(f"[_extract_jobs_from_html] failed on {url}: {e}")

        if jobs:
            break

    return jobs


def get_ats_postings(ats_url: str) -> list[dict]:
    if not ats_url:
        return []

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

    # If no standard ATS API matched or returned empty, try HTML careers fallback
    if not metadata:
        metadata = _extract_jobs_from_html(ats_url)

    return metadata