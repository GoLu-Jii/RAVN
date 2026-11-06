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
from zoneinfo import ZoneInfo

from app.daily.dates import _parse_date

MAX_DETAIL_LOOKUPS = 20

def _extract_board_token(ats_url: str) -> str | None:
    """
    Pulls the company/board token out of the URL's last path segment.
    e.g. https://boards.greenhouse.io/stripe -> "stripe"
    """
    path = ats_url.rstrip("/").split("/")
    if not path or not path[-1]:
        return None
    return path[-1]


def _greenhouse_job_date(token: str, job_id: int, strict: bool = False) -> tuple[str | None, str]:
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}"
    try:
        response = httpx.get(api_url, timeout=8)
        response.raise_for_status()
        value = response.json().get("first_published")
        if value and _parse_date(value):
            return value, "dated"
        return (None, "undated") if not value else (None, "not_checked")
    except (httpx.HTTPError, ValueError, AttributeError):
        if strict:
            raise
        return None, "not_checked"


def fetch_greenhouse_jobs(
    ats_url: str, strict: bool = False, collection_date=None,
    known_urls: set[str] | None = None,
) -> list[dict]:
    token = _extract_board_token(ats_url)
    if not token:
        return []

    api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

    try:
        response = httpx.get(api_url, timeout=10)
    except httpx.RequestError as e:
        if strict:
            raise
        print(f"[fetch_greenhouse_jobs] network error for {token}: {e}")
        return []

    if response.status_code != 200:
        if strict:
            response.raise_for_status()
        print(f"[fetch_greenhouse_jobs] {token} returned {response.status_code}")
        return []

    jobs = response.json().get("jobs", [])

    output = []
    details_checked = 0
    for job in jobs:
        updated_at = job.get("updated_at")
        if collection_date:
            updated_datetime = _parse_date(updated_at)
            if not updated_datetime or updated_datetime.astimezone(ZoneInfo("Asia/Kolkata")).date() != collection_date:
                continue
        first_published = job.get("first_published") if not collection_date else None
        date_state = "dated" if first_published and _parse_date(first_published) else "undated"
        if collection_date and job.get("absolute_url") in (known_urls or set()):
            date_state = "not_checked"
        elif collection_date and details_checked < MAX_DETAIL_LOOKUPS:
            job_id = job.get("id")
            if job_id is not None:
                details_checked += 1
                first_published, date_state = _greenhouse_job_date(token, job_id, strict=False)
        elif collection_date:
            date_state = "not_checked"
        output.append({
            "id": job.get("id"),
            "title": job.get("title"),
            "location": (job.get("location") or {}).get("name"),
            "url": job.get("absolute_url"),
            "posted_at": first_published,
            "date_state": date_state,
        })
    return output


def fetch_lever_jobs(ats_url: str, strict: bool = False) -> list[dict]:
    token = _extract_board_token(ats_url)
    if not token:
        return []

    api_url = f"https://api.lever.co/v0/postings/{token}?mode=json"

    try:
        response = httpx.get(api_url, timeout=10)
    except httpx.RequestError as e:
        if strict:
            raise
        print(f"[fetch_lever_jobs] network error for {token}: {e}")
        return []

    if response.status_code != 200:
        if strict:
            response.raise_for_status()
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


def fetch_ashbyhq_jobs(ats_url: str, strict: bool = False) -> list[dict]:
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
        if strict:
            raise
        print(f"[fetch_ashbyhq_jobs] network error for {token}: {e}")
        return []

    if response.status_code != 200:
        if strict:
            response.raise_for_status()
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


def fetch_workable_jobs(ats_url: str, strict: bool = False) -> list[dict]:
    token = _extract_board_token(ats_url)
    if not token:
        return []

    api_url = f"https://www.workable.com/api/accounts/{token}?details=true"

    try:
        response = httpx.get(api_url, timeout=10)
    except httpx.RequestError as e:
        if strict:
            raise
        print(f"[fetch_workable_jobs] network error for {token}: {e}")
        return []

    if response.status_code != 200:
        if strict:
            response.raise_for_status()
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


def _jsonld_date_posted(payload):
    """Find datePosted on JobPosting objects, including lists and @graph blocks."""
    if isinstance(payload, list):
        for item in payload:
            found = _jsonld_date_posted(item)
            if found:
                return found
    elif isinstance(payload, dict):
        types = payload.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if any(value == "JobPosting" or str(value).rstrip("/").endswith("/JobPosting") for value in types) and payload.get("datePosted"):
            return payload["datePosted"]
        for value in payload.values():
            if isinstance(value, (list, dict)):
                found = _jsonld_date_posted(value)
                if found:
                    return found
    return None


def _job_page_date_result(url: str) -> tuple[str | None, str]:
    import json
    from bs4 import BeautifulSoup

    try:
        response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; RAVN/1.0)"}, timeout=8, follow_redirects=True)
        if response.status_code != 200:
            return None, "not_checked"
        soup = BeautifulSoup(response.text, "html.parser")
        invalid_date_found = False
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                payload = json.loads(script.string or script.get_text())
                value = _jsonld_date_posted(payload)
                if value and _parse_date(value):
                    return value, "dated"
                if value:
                    invalid_date_found = True
            except (json.JSONDecodeError, TypeError):
                continue
        return (None, "not_checked") if invalid_date_found else (None, "undated")
    except httpx.RequestError:
        return None, "not_checked"


def _job_page_date(url: str) -> str | None:
    return _job_page_date_result(url)[0]


def _extract_jobs_from_html(
    ats_url: str, max_jobs: int = 30, strict: bool = False,
    known_urls: set[str] | None = None,
) -> list[dict]:
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
    known_urls = known_urls or set()
    seen = set()
    fetched_page = False
    last_error = None

    for url in urls_to_try:
        try:
            r = httpx.get(url, headers=headers, timeout=8, follow_redirects=True)
            if r.status_code != 200:
                last_error = httpx.HTTPStatusError(
                    f"Career page returned {r.status_code}", request=r.request, response=r
                )
                continue
            fetched_page = True
            soup = BeautifulSoup(r.text, "html.parser")

            # 1. Check if page references an external ATS (Greenhouse, Lever, etc.)
            for a in soup.find_all("a"):
                href = a.get("href", "")
                ext_platform = detect_ats_platform(href)
                if ext_platform:
                    ext_jobs = get_ats_postings(href, strict=strict)
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
                    job = {
                        "title": text,
                        "location": None,
                        "url": urljoin(url, href),
                        "posted_at": None,
                    }
                    jobs.append(job)

                if len(jobs) >= max_jobs:
                    break
        except Exception as e:
            last_error = e
            if strict:
                continue
            print(f"[_extract_jobs_from_html] failed on {url}: {e}")

        if jobs:
            break

    # Read dates only from unseen detail pages. Limit requests per collection.
    details_checked = 0
    for job in jobs:
        if job["url"] in known_urls:
            job["date_state"] = "not_checked"
            continue
        if details_checked >= MAX_DETAIL_LOOKUPS:
            job["date_state"] = "not_checked"
            continue
        details_checked += 1
        job["posted_at"], job["date_state"] = _job_page_date_result(job["url"])

    if strict and not fetched_page and last_error:
        raise last_error
    return jobs


def get_ats_postings(
    ats_url: str, strict: bool = False, known_urls: set[str] | None = None,
    collection_date=None,
) -> list[dict]:
    if not ats_url:
        return []

    platform = detect_ats_platform(ats_url)
    
    api_error = None
    try:
        if platform == "Greenhouse":
            metadata = fetch_greenhouse_jobs(
                ats_url, strict=strict, collection_date=collection_date,
                known_urls=known_urls,
            )
        elif platform == "lever":
            metadata = fetch_lever_jobs(ats_url, strict=strict)
        elif platform == "ashbyhq":
            metadata = fetch_ashbyhq_jobs(ats_url, strict=strict)
        elif platform == "workable":
            metadata = fetch_workable_jobs(ats_url, strict=strict)
        else:
            metadata = []
    except Exception as exc:
        if not strict:
            raise
        api_error = exc
        metadata = []

    # If no standard ATS API matched or returned empty, try HTML careers fallback
    if not metadata:
        try:
            metadata = _extract_jobs_from_html(ats_url, strict=strict, known_urls=known_urls)
        except Exception:
            if api_error:
                raise api_error
            raise

    if strict and api_error and not metadata:
        raise api_error

    known_urls = known_urls or set()
    for item in metadata:
        if item.get("url") in known_urls:
            item["date_state"] = "not_checked"
        elif "date_state" not in item:
            item["date_state"] = (
                "dated" if item.get("posted_at") and _parse_date(item["posted_at"])
                else "undated" if not item.get("posted_at")
                else "not_checked"
            )

    return metadata
