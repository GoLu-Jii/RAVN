"""Small, read-only tools for the target-scoped Company Scout agent."""

from datetime import date, datetime, timedelta, timezone
from copy import deepcopy
import ipaddress
import os
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from app.db.database import SessionLocal
from app.targets.models import Target


TIMEZONE = ZoneInfo("Asia/Kolkata")
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
REQUEST_TIMEOUT = 10
MAX_RESPONSE_BYTES = 1_000_000
MAX_PAGE_TEXT = 8_000
MAX_PAGE_LINKS = 30
MAX_UNIQUE_PAGE_READS = 20
MAX_VIDEO_PAGES = 10
MAX_TRANSCRIPT_CHARS = 12_000
MAX_REDIRECTS = 5
BLOCKED_SOCIAL_HOSTS = {"instagram.com", "x.com", "twitter.com"}


def _target_snapshot(target_id: int) -> dict[str, Any] | None:
    with SessionLocal() as db:
        target = db.query(Target).filter_by(id=target_id).first()
        if target is None:
            return None
        return {
            "website_url": target.website_url,
            "youtube_url": target.youtube_url,
        }


def _date_value(value: str) -> date:
    return date.fromisoformat(value)


def _published_date(value: str) -> date:
    published = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published.astimezone(TIMEZONE).date()


def _youtube_request(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    response = httpx.get(
        f"{YOUTUBE_API}/{endpoint}",
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _channel_lookup(youtube_url: str, api_key: str) -> tuple[str, str]:
    """Resolve a supplied channel URL to its ID and uploads playlist ID."""
    parsed = urlsplit(youtube_url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in {"youtube.com", "m.youtube.com"}:
        raise ValueError("youtube_url must be a YouTube channel URL.")

    parts = [part for part in parsed.path.split("/") if part]
    lookup: dict[str, str]
    if len(parts) >= 2 and parts[0] == "channel" and re.fullmatch(r"UC[\w-]+", parts[1]):
        lookup = {"id": parts[1]}
    elif parts and parts[0].startswith("@"):
        lookup = {"forHandle": parts[0]}
    elif len(parts) >= 2 and parts[0] == "user":
        lookup = {"forUsername": parts[1]}
    else:
        raise ValueError("Use a YouTube channel URL with an @handle or channel ID.")

    data = _youtube_request(
        "channels",
        {"part": "contentDetails", **lookup, "key": api_key},
    )
    items = data.get("items", [])
    if not items:
        raise LookupError("YouTube channel was not found.")
    channel = items[0]
    channel_id = channel.get("id")
    uploads_id = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not channel_id or not uploads_id:
        raise LookupError("YouTube channel did not provide an uploads playlist.")
    return channel_id, uploads_id


def _video_details(video_ids: list[str], api_key: str) -> list[dict[str, Any]]:
    if not video_ids:
        return []
    data = _youtube_request(
        "videos",
        {"part": "snippet", "id": ",".join(video_ids), "key": api_key},
    )
    return data.get("items", [])


def _week_bounds(week_start: str, week_end: str) -> tuple[date, date]:
    start = _date_value(week_start)
    end = _date_value(week_end)
    if start.weekday() != 0 or end != start + timedelta(days=6):
        raise ValueError("Expected a Monday-to-Sunday week window.")
    return start, end


def _safe_http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        host = parsed.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False
        if any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_SOCIAL_HOSTS):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return True
        return address.is_global
    except (TypeError, ValueError):
        return False


def _public_http_url(url: str) -> bool:
    if not _safe_http_url(url):
        return False
    host = urlsplit(url).hostname
    try:
        addresses = {
            ipaddress.ip_address(result[4][0])
            for result in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        }
        return bool(addresses) and all(address.is_global for address in addresses)
    except (OSError, ValueError):
        return False


def _url_key(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def _fetch_html(url: str) -> tuple[str, bytes, str]:
    current_url = url
    headers = {"User-Agent": "RAVN-CompanyScout/1.0", "Accept": "text/html"}
    with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=False, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            if not _public_http_url(current_url):
                raise ValueError("URL must use public HTTP or HTTPS.")
            with client.stream("GET", current_url) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                if "html" not in response.headers.get("content-type", "").lower():
                    raise ValueError("URL did not return an HTML page.")
                body = bytearray()
                for chunk in response.iter_bytes(8192):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ValueError("Page exceeded the response-size limit.")
                encoding = response.encoding or "utf-8"
                return str(response.url), bytes(body), encoding
    raise ValueError("Page redirected too many times.")


def _published_metadata(soup: BeautifulSoup) -> str | None:
    for selector, attribute in (
        ('meta[property="article:published_time"]', "content"),
        ('meta[name="date"]', "content"),
        ('meta[name="publish-date"]', "content"),
        ("time[datetime]", "datetime"),
    ):
        element = soup.select_one(selector)
        value = element.get(attribute) if element else None
        if value:
            return value.strip()
    return None


def build_company_scout_tools(target_id: int):
    """Bind three read-only tools to one target and its supplied source URLs."""
    target = _target_snapshot(target_id)
    if target is None:
        raise ValueError(f"Target {target_id} does not exist.")

    target_website = target["website_url"]
    youtube_url = target["youtube_url"]
    allowed_page_urls = {_url_key(target_website)}
    target_host = (urlsplit(target_website).hostname or "").lower().rstrip(".")
    page_cache: dict[str, dict[str, Any]] = {}
    attempted_page_urls: set[str] = set()
    weekly_video_ids: set[str] = set()

    @tool("get_weekly_youtube_videos")
    def get_weekly_youtube_videos(week_start: str, week_end: str) -> dict[str, Any]:
        """Get this target channel's public videos published during the supplied week."""
        weekly_video_ids.clear()
        if not youtube_url:
            return {"status": "not_configured", "items": [], "count": 0}
        api_key = os.getenv("YOUTUBE_API_KEY")
        if not api_key:
            return {"status": "unavailable", "items": [], "count": 0, "diagnostic": "youtube_api_key_missing"}
        try:
            start, end = _week_bounds(week_start, week_end)
            channel_id, uploads_id = _channel_lookup(youtube_url, api_key)
            items = []
            page_token = None
            pages_checked = 0
            has_more = False

            while pages_checked < MAX_VIDEO_PAGES:
                params = {
                    "part": "snippet,contentDetails",
                    "playlistId": uploads_id,
                    "maxResults": 50,
                    "key": api_key,
                }
                if page_token:
                    params["pageToken"] = page_token
                page = _youtube_request("playlistItems", params)
                pages_checked += 1
                page_ids = [
                    row.get("contentDetails", {}).get("videoId")
                    for row in page.get("items", [])
                ]
                page_ids = [video_id for video_id in page_ids if video_id]
                videos = _video_details(page_ids, api_key)
                page_dates = []
                for video in videos:
                    snippet = video.get("snippet", {})
                    published_at = snippet.get("publishedAt")
                    if not published_at or snippet.get("channelId") != channel_id:
                        continue
                    published_day = _published_date(published_at)
                    page_dates.append(published_day)
                    if start <= published_day <= end:
                        video_id = video.get("id")
                        if not video_id:
                            continue
                        weekly_video_ids.add(video_id)
                        items.append({
                            "video_id": video_id,
                            "title": snippet.get("title", ""),
                            "description": snippet.get("description", ""),
                            "published_at": published_at,
                            "url": f"https://www.youtube.com/watch?v={video_id}",
                        })

                page_token = page.get("nextPageToken")
                has_more = bool(page_token)
                if not page_token or (page_dates and max(page_dates) < start):
                    has_more = False
                    break

            items.sort(key=lambda item: item["published_at"], reverse=True)
            return {
                "status": "partial" if has_more else "checked",
                "items": items,
                "count": len(items),
                "pages_checked": pages_checked,
            }
        except (ValueError, LookupError) as exc:
            weekly_video_ids.clear()
            return {"status": "unavailable", "items": [], "count": 0, "diagnostic": str(exc)}
        except Exception as exc:
            weekly_video_ids.clear()
            return {"status": "unavailable", "items": [], "count": 0, "diagnostic": type(exc).__name__}

    @tool("get_youtube_transcript")
    def get_youtube_transcript(video_id: str) -> dict[str, Any]:
        """Get an available transcript for a video returned by the weekly video tool."""
        if video_id not in weekly_video_ids:
            return {"status": "invalid_input", "video_id": video_id, "diagnostic": "video_must_come_from_weekly_video_results"}
        try:
            from youtube_transcript_api import YouTubeTranscriptApi

            transcript_list = YouTubeTranscriptApi().list(video_id)
            tracks = list(transcript_list)
            if not tracks:
                return {"status": "unavailable", "video_id": video_id, "diagnostic": "transcript_not_available"}
            track = next((item for item in tracks if item.language_code.lower().startswith("en")), tracks[0])
            fetched = track.fetch()
            text = " ".join(item.text for item in fetched)
            return {
                "status": "checked",
                "video_id": video_id,
                "language": track.language,
                "language_code": track.language_code,
                "text": text[:MAX_TRANSCRIPT_CHARS],
                "truncated": len(text) > MAX_TRANSCRIPT_CHARS,
            }
        except Exception as exc:
            return {"status": "unavailable", "video_id": video_id, "diagnostic": type(exc).__name__}

    @tool("read_webpage")
    def read_webpage(url: str) -> dict[str, Any]:
        """Read a page on the target website or an external link returned by this tool."""
        key = _url_key(url)
        if key in page_cache:
            result = deepcopy(page_cache[key])
            result["cached"] = True
            return result

        requested_host = (urlsplit(url).hostname or "").lower().rstrip(".")
        is_target_site = requested_host == target_host
        if key not in allowed_page_urls and not is_target_site:
            return {"status": "rejected", "url": url, "diagnostic": "url_not_from_target_website_or_a_returned_link"}
        if key not in attempted_page_urls and len(attempted_page_urls) >= MAX_UNIQUE_PAGE_READS:
            return {
                "status": "limit_reached",
                "url": url,
                "pages_read": len(attempted_page_urls),
                "diagnostic": "unique_page_read_limit_reached",
            }
        attempted_page_urls.add(key)
        try:
            final_url, body, encoding = _fetch_html(url)
            html = body.decode(encoding, errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            for element in soup(["script", "style", "noscript", "svg"]):
                element.decompose()
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            text = soup.get_text(" ", strip=True)
            if not text:
                result = {"status": "unavailable", "url": final_url, "diagnostic": "no_static_html_text"}
                page_cache[key] = result
                return deepcopy(result)

            links = []
            for anchor in soup.find_all("a", href=True):
                link_url = urljoin(final_url, anchor["href"].strip())
                if not _safe_http_url(link_url):
                    continue
                link_url = link_url.split("#", 1)[0]
                link_key = _url_key(link_url)
                if link_key in allowed_page_urls:
                    continue
                allowed_page_urls.add(link_key)
                links.append({"url": link_url, "text": anchor.get_text(" ", strip=True)[:160]})
                if len(links) >= MAX_PAGE_LINKS:
                    break

            allowed_page_urls.add(_url_key(final_url))
            result = {
                "status": "checked",
                "url": final_url,
                "title": title,
                "published_at": _published_metadata(soup),
                "text": text[:MAX_PAGE_TEXT],
                "truncated": len(text) > MAX_PAGE_TEXT,
                "links": links,
            }
            page_cache[key] = result
            page_cache[_url_key(final_url)] = result
            return deepcopy(result)
        except Exception as exc:
            result = {"status": "unavailable", "url": url, "diagnostic": type(exc).__name__}
            page_cache[key] = result
            return deepcopy(result)

    return [get_weekly_youtube_videos, get_youtube_transcript, read_webpage]
