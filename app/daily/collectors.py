import hashlib
import json
import logging
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import httpx
import trafilatura
from sqlalchemy.orm import Session

from app.daily.fetchers.ats import get_ats_postings
from app.daily.fetchers.blogs import get_blog_posts
from app.daily.fetchers.github import get_new_commits
from app.daily.fetchers.socials import get_web_social_content
from app.daily.dates import _as_utc, _parse_date
from app.events.models import DailySourceCheckpoint, DailySourceSummary, Event
from app.targets.models import Target


logger = logging.getLogger(__name__)
SOURCE_FIELDS = {
    "github": "github_url",
    "ats": "ats_url",
    "blog": "blog_url",
    "web_social": "web_social_url",
}
COLLECTION_TIMEZONE = ZoneInfo("Asia/Kolkata")


def _fetch_full_text(url: str | None, fallback: str = "") -> str:
    if not url:
        return fallback
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RAVN/1.0)"},
            timeout=12,
            follow_redirects=True,
        )
        if response.status_code == 200:
            text = trafilatura.extract(response.text)
            if text:
                return text
    except (httpx.RequestError, ValueError) as exc:
        logger.info("Could not fetch full source text from %s: %s", url, exc)
    return fallback


def _candidate_events(
    db: Session, target: Target, source_type: str, since: datetime,
    collection_date: date | None = None,
) -> list[dict]:
    if source_type == "github":
        return [
            {
                "source_key": item["sha"],
                "title": f"Commit in {item['repo']}",
                "url": item.get("url"),
                "event_date": _parse_date(item.get("date")),
                "raw_content": json.dumps(item, ensure_ascii=False),
                "one_liner": f"{item['repo']}: {(item.get('message') or 'Commit made').splitlines()[0][:420]}",
                "tags": ["github", "commit"],
            }
            for item in get_new_commits(target.github_url, since)
            if item.get("sha")
        ]

    if source_type == "ats":
        events = []
        known_urls = {
            row[0] for row in db.query(Event.url).filter(
                Event.target_id == target.id,
                Event.source_type == "ats",
                Event.url.is_not(None),
            ).all()
        }
        for item in get_ats_postings(
            target.ats_url, strict=True, known_urls=known_urls,
            collection_date=collection_date,
        ):
            url = item.get("url")
            key = url or f"{item.get('title', '')}:{item.get('location', '')}"
            source_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
            date_state = item.get("date_state")
            if url in known_urls:
                date_state = "not_checked"
            elif date_state is None:
                date_state = "dated" if _parse_date(item.get("posted_at")) else "undated"
            if not url and _event_exists(db, target.id, source_type, source_key):
                continue
            raw = "\n".join(filter(None, [
                f"Title: {item.get('title')}",
                f"Location: {item.get('location')}",
                f"URL: {url}",
            ]))
            events.append({
                "source_key": source_key,
                "title": item.get("title") or "New job posting",
                "url": url,
                "event_date": _parse_date(item.get("posted_at")),
                "date_state": date_state,
                "raw_content": raw,
                "one_liner": f"New role posted: {item.get('title') or 'Untitled role'}"
                + (f" ({item['location']})" if item.get("location") else ""),
                "tags": ["hiring", "job_posting"],
            })
        return events

    if source_type == "blog":
        events = []
        known_urls = {
            row[0] for row in db.query(Event.url).filter(
                Event.target_id == target.id,
                Event.source_type == "blog",
                Event.url.is_not(None),
            ).all()
        }
        for item in get_blog_posts(
            target.blog_url, include_content=False, strict=True,
            include_metadata=True, known_urls=known_urls,
        ):
            url = item.get("link")
            if not url:
                continue
            source_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
            date_state = item.get("date_state")
            if url in known_urls:
                date_state = "not_checked"
            elif date_state is None:
                date_state = "dated" if _parse_date(item.get("published")) else "undated"
            if url not in known_urls and _event_exists(db, target.id, source_type, source_key):
                continue
            events.append({
                "source_key": source_key,
                "title": item.get("title") or "New blog post",
                "url": url,
                "event_date": _parse_date(item.get("published")),
                "date_state": date_state,
                "raw_content": item.get("content") or item.get("title") or "",
                "one_liner": f"New post: {item.get('title') or url}",
                "tags": ["engineering", "blog"],
            })
        return events

    content = get_web_social_content(target.web_social_url, strict=True)
    if not content:
        return []
    text = content["content"]
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return [{
        "source_key": content_hash,
        "content_hash": content_hash,
        "title": "Public web page changed",
        "url": content["url"],
        "event_date": None,
        "raw_content": text,
        "one_liner": f"Public page content changed: {content['url']}",
        "tags": ["web_update"],
    }]


def _event_exists(db: Session, target_id: int, source_type: str, source_key: str) -> bool:
    return db.query(Event.id).filter_by(
        target_id=target_id,
        source_type=source_type,
        source_key=source_key,
    ).first() is not None


def _day_start_utc(collection_date: date) -> datetime:
    return datetime.combine(collection_date, time.min, COLLECTION_TIMEZONE).astimezone(timezone.utc)


def _save_daily_summary(
    db: Session,
    target_id: int,
    collection_date: date,
    source_type: str,
    status: str,
    error: str | None = None,
    undated_items: int = 0,
    highlight_override: str | None = None,
) -> DailySourceSummary:
    summary = db.query(DailySourceSummary).filter_by(
        target_id=target_id,
        collection_date=collection_date,
        source_type=source_type,
    ).first()
    if summary is None:
        summary = DailySourceSummary(
            target_id=target_id,
            collection_date=collection_date,
            source_type=source_type,
            status=status,
            event_count=0,
            highlight="",
            collected_at=datetime.now(timezone.utc),
        )
        db.add(summary)

    summary.status = status
    summary.error = error
    summary.collected_at = datetime.now(timezone.utc)
    summary.undated_items = undated_items
    if status == "failed":
        if not summary.highlight:
            summary.highlight = "Collection failed for this source."
    else:
        events = (
            db.query(Event)
            .filter_by(
                target_id=target_id,
                collection_date=collection_date,
                source_type=source_type,
                is_baseline=False,
            )
            .order_by(Event.event_date.desc(), Event.id.desc())
            .all()
        )
        summary.event_count = len(events)
        if highlight_override:
            summary.highlight = highlight_override
        elif status == "skipped":
            summary.highlight = "Source is not configured for this target."
        elif events:
            highlights = [event.one_liner for event in events[:3]]
            remaining = len(events) - len(highlights)
            summary.highlight = "; ".join(highlights)
            if remaining:
                summary.highlight += f"; and {remaining} more event(s)"
        else:
            summary.highlight = "No events found for this source today."
    return summary


def collect_target_events(db: Session, target: Target, collection_date: date | None = None) -> dict:
    """Collect only this Asia/Kolkata calendar date and update its four source summaries."""
    collection_date = collection_date or datetime.now(COLLECTION_TIMEZONE).date()
    is_past_date = collection_date < datetime.now(COLLECTION_TIMEZONE).date()
    since = _day_start_utc(collection_date)
    result = {"target_id": target.id, "collection_date": collection_date, "sources": {}}
    for source_type, field in SOURCE_FIELDS.items():
        source_url = getattr(target, field)
        if not source_url:
            summary = _save_daily_summary(db, target.id, collection_date, source_type, "skipped")
            db.commit()
            result["sources"][source_type] = {
                "status": "skipped", "new_events": 0, "event_count": summary.event_count,
                "highlight": summary.highlight, "undated_items": 0, "not_checked_items": 0,
            }
            continue

        if is_past_date and source_type == "web_social":
            message = "Skipped: web/social snapshots cannot be reconstructed for a past date."
            summary = _save_daily_summary(
                db, target.id, collection_date, source_type, "skipped",
                highlight_override=message,
            )
            db.commit()
            result["sources"][source_type] = {
                "status": "skipped", "new_events": 0,
                "event_count": summary.event_count, "highlight": summary.highlight,
                "undated_items": 0, "not_checked_items": 0,
            }
            continue

        checkpoint = (
            db.query(DailySourceCheckpoint)
            .filter_by(target_id=target.id, source_type=source_type)
            .first()
        )
        first_run = checkpoint is None
        first_undated_snapshot = (
            source_type in {"ats", "blog"}
            and (checkpoint is None or checkpoint.undated_items_baselined_at is None)
        )
        inserted = 0
        baseline_inserted = 0
        undated_items = 0
        not_checked_items = 0
        ignored_out_of_day = 0
        try:
            candidates = _candidate_events(db, target, source_type, since, collection_date)
            for item in candidates:
                source_event_date = item["event_date"]
                observed_at = datetime.now(timezone.utc)

                if source_type == "web_social":
                    content_hash = item["content_hash"]
                    if checkpoint and checkpoint.last_content_hash == content_hash:
                        continue
                    item["source_key"] = hashlib.sha256(
                        f"{collection_date}:{content_hash}:{observed_at.isoformat()}".encode("utf-8")
                    ).hexdigest()
                    event_date = observed_at
                else:
                    date_state = item.get("date_state")
                    if source_type in {"ats", "blog"}:
                        if date_state is None:
                            date_state = "dated" if source_event_date else "undated"
                        if date_state == "not_checked" or (date_state == "dated" and source_event_date is None):
                            not_checked_items += 1
                            continue
                        if date_state == "undated":
                            undated_items += 1
                            if is_past_date:
                                continue
                            event_date = observed_at
                        else:
                            event_date = _as_utc(source_event_date)
                            if event_date.astimezone(COLLECTION_TIMEZONE).date() != collection_date:
                                ignored_out_of_day += 1
                                continue
                    elif source_event_date is None:
                        undated_items += 1
                        if is_past_date:
                            continue
                        event_date = observed_at
                    else:
                        event_date = _as_utc(source_event_date)
                        if event_date.astimezone(COLLECTION_TIMEZONE).date() != collection_date:
                            ignored_out_of_day += 1
                            continue

                    # Fetch long-form content only after confirming that the
                    # dated source timestamp belongs to this collection date.
                    if source_event_date is not None and source_type == "ats":
                        details = _fetch_full_text(item["url"])
                        if details:
                            item["raw_content"] += f"\nDescription: {details}"
                    elif source_event_date is not None and source_type == "blog":
                        if not item.get("content"):
                            item["raw_content"] = _fetch_full_text(
                                item["url"], item["raw_content"]
                            ) or item["raw_content"]

                exists = db.query(Event.id).filter_by(
                    target_id=target.id,
                    source_type=source_type,
                    source_key=item["source_key"],
                ).first()
                if exists:
                    continue
                is_baseline = (
                    first_run and source_type == "web_social" and source_event_date is None
                ) or (
                    first_undated_snapshot
                    and source_type in {"ats", "blog"}
                    and source_event_date is None
                )
                db.add(Event(
                    target_id=target.id,
                    source_type=source_type,
                    source_key=item["source_key"],
                    title=item["title"],
                    url=item["url"],
                    raw_content=item["raw_content"],
                    one_liner=item["one_liner"],
                    collection_date=collection_date,
                    event_date=event_date,
                    tags=item["tags"],
                    is_baseline=is_baseline,
                    date_basis=("first_observed" if source_event_date is None or source_type == "web_social" else "source"),
                ))
                inserted += 1
                if is_baseline:
                    baseline_inserted += 1

            now = datetime.now(timezone.utc)
            if checkpoint is None:
                checkpoint = DailySourceCheckpoint(
                    target_id=target.id,
                    source_type=source_type,
                    last_success_at=now,
                )
                db.add(checkpoint)
            else:
                checkpoint.last_success_at = now
            if (
                not is_past_date
                and source_type in {"ats", "blog"}
                and checkpoint.undated_items_baselined_at is None
            ):
                checkpoint.undated_items_baselined_at = now
            if source_type == "web_social" and candidates:
                checkpoint.last_content_hash = candidates[-1]["content_hash"]
            db.flush()
            summary = _save_daily_summary(
                db, target.id, collection_date, source_type, "success",
                undated_items=undated_items,
            )
            db.commit()
            result["sources"][source_type] = {
                "status": "success",
                "new_events": inserted - baseline_inserted,
                "baseline_events": baseline_inserted,
                "event_count": summary.event_count,
                "highlight": summary.highlight,
                "undated_items": undated_items,
                "not_checked_items": not_checked_items,
                "ignored_out_of_day": ignored_out_of_day,
            }
        except Exception as exc:
            db.rollback()
            logger.exception("Daily collection failed for target %s source %s", target.id, source_type)
            summary = _save_daily_summary(db, target.id, collection_date, source_type, "failed", str(exc))
            db.commit()
            result["sources"][source_type] = {
                "status": "failed",
                "new_events": 0,
                "event_count": summary.event_count,
                "highlight": summary.highlight,
                "undated_items": 0,
                "not_checked_items": not_checked_items,
                "error": str(exc),
            }
    return result
