from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.routes import get_current_user
from app.daily.collectors import collect_target_events
from app.db.database import get_db
from app.events.models import DailySourceSummary, Event
from app.targets.models import Target


router = APIRouter(prefix="/targets/{target_id}/daily", tags=["3. Daily Events"])
SOURCE_TYPES = ("github", "ats", "blog", "web_social")
COLLECTION_TIMEZONE = ZoneInfo("Asia/Kolkata")


def _get_owned_target(target_id: int, user_id: int, db: Session) -> Target:
    target = db.query(Target).filter_by(id=target_id, user_id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    return target


def _serialize_event(event: Event, include_content: bool = False) -> dict:
    result = {
        "id": event.id,
        "title": event.title,
        "source_type": event.source_type,
        "url": event.url,
        "one_liner": event.one_liner,
        "collection_date": event.collection_date,
        "event_date": event.event_date,
        "date_basis": event.date_basis,
        "ingested_at": event.ingested_at,
        "tags": event.tags,
    }
    if include_content:
        result["raw_content"] = event.raw_content
    return result


@router.post(
    "/collect",
    summary="Collect from all configured sources",
    description=(
        "Enter the target ID only. Runs every configured source (GitHub, ATS, blog, and web/social) "
        "and stores newly discovered events. Source failures are reported independently."
    ),
)
def collect_daily_events(
    target_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _get_owned_target(target_id, current_user.id, db)
    if target.status != "ready":
        raise HTTPException(status_code=409, detail="Target Kundali must be ready before daily collection")
    return collect_target_events(db, target)


@router.get(
    "/events",
    summary="List daily event feed (read only)",
    description=(
        "Returns stored events without starting a scrape. Leave source_type and since empty to list "
        "events from all sources; use them only to filter the feed. To scrape all configured sources, "
        "use POST /targets/{target_id}/daily/collect."
    ),
)
def list_daily_events(
    target_id: int,
    source_type: str | None = Query(None, pattern="^(github|ats|blog|web_social)$"),
    collection_date: date | None = None,
    since: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_target(target_id, current_user.id, db)
    query = db.query(Event).filter(
        Event.target_id == target_id,
        Event.is_baseline.is_(False),
    )
    if source_type:
        query = query.filter(Event.source_type == source_type)
    if collection_date:
        query = query.filter(Event.collection_date == collection_date)
    if since:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        query = query.filter(Event.event_date >= since)

    total = query.count()
    events = (
        query.order_by(Event.event_date.desc(), Event.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "target_id": target_id,
        "items": [_serialize_event(event) for event in events],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/summaries",
    summary="Get the four source highlights for a date",
    description=(
        "Returns one daily status and highlight for GitHub, ATS, blog, and web/social. "
        "Defaults to today's date in Asia/Kolkata; pass collection_date to view another saved day."
    ),
)
def get_daily_summaries(
    target_id: int,
    collection_date: date | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_target(target_id, current_user.id, db)
    collection_date = collection_date or datetime.now(COLLECTION_TIMEZONE).date()
    rows = db.query(DailySourceSummary).filter_by(
        target_id=target_id,
        collection_date=collection_date,
    ).all()
    summaries_by_source = {row.source_type: row for row in rows}
    return {
        "target_id": target_id,
        "collection_date": collection_date,
        "sources": [
            {
                "source_type": source_type,
                "status": summaries_by_source[source_type].status if source_type in summaries_by_source else "not_recorded",
                "event_count": summaries_by_source[source_type].event_count if source_type in summaries_by_source else 0,
                "undated_items": summaries_by_source[source_type].undated_items if source_type in summaries_by_source else 0,
                "highlight": summaries_by_source[source_type].highlight if source_type in summaries_by_source else "No summary was recorded for this source and date; collection status is unknown.",
                "error": summaries_by_source[source_type].error if source_type in summaries_by_source else None,
                "collected_at": summaries_by_source[source_type].collected_at if source_type in summaries_by_source else None,
            }
            for source_type in SOURCE_TYPES
        ],
    }


@router.get(
    "/summaries/dates",
    summary="List dates with daily source summaries",
)
def list_daily_summary_dates(
    target_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_target(target_id, current_user.id, db)
    dates = (
        db.query(DailySourceSummary.collection_date)
        .filter(DailySourceSummary.target_id == target_id)
        .distinct()
        .order_by(DailySourceSummary.collection_date.desc())
        .all()
    )
    return {"target_id": target_id, "dates": [row[0] for row in dates]}


@router.get("/events/{event_id}", summary="Get daily event details")
def get_daily_event(
    target_id: int,
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_target(target_id, current_user.id, db)
    event = db.query(Event).filter(
        Event.id == event_id,
        Event.target_id == target_id,
        Event.is_baseline.is_(False),
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return _serialize_event(event, include_content=True)
