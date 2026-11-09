"""Build a complete, JSON-safe weekly analysis input with explicit compaction."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import math
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.embeddings.text import build_embedding_text
from app.events.models import DailySourceSummary, Event
from app.kundali.models import Kundali
from app.targets.models import Target
from app.weekly.config import WEEKLY_CONFIG, WeeklyConfig
from app.weekly.coverage import SOURCES, source_coverage
from app.weekly.counts import weekly_event_counts
from app.weekly.prompt import PROMPT_VERSION
from app.weekly.retrieval import retrieve_history
from app.weekly.windows import previous_week_starts, require_monday, week_window


SHORT_TEXT_WORDS = 60


class WeeklyInputError(ValueError):
    def __init__(self, reason: str, token_estimate: int, token_budget: int):
        super().__init__(reason)
        self.reason = reason
        self.token_estimate = token_estimate
        self.token_budget = token_budget


def estimate_tokens(value) -> int:
    """Conservative, replaceable estimate: serialized characters divided by four."""
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return math.ceil(len(serialized) / 4)


def _truncate_words(value: str | None, limit: int) -> str:
    return " ".join((value or "").split()[:limit])


def _current_event(event: Event) -> dict:
    return {
        "event_id": event.id,
        "source": event.source_type,
        "collection_date": event.collection_date.isoformat(),
        "date_basis": event.date_basis or "source",
        "title": event.title,
        "one_liner": event.one_liner,
        "text": build_embedding_text(event),
    }


def _baseline_context(kundali: Kundali | None) -> dict:
    note = (
        "Kundali baseline context is an undated snapshot, not weekly event evidence; "
        "source depth varies."
    )
    if kundali is None:
        return {"available": False, "note": note}

    tech_stack = kundali.tech_stack or {}
    top_tech = dict(sorted(tech_stack.items(), key=lambda pair: (-pair[1], str(pair[0])))[:8])
    raw_cadence = kundali.cadence_baseline or {}
    cadence = [int(raw_cadence.get(str(index), raw_cadence.get(index, 0)) or 0) for index in range(52)]
    jobs = kundali.ats_postings if isinstance(kundali.ats_postings, list) else []
    blog = kundali.blog_status if isinstance(kundali.blog_status, list) else []
    return {
        "available": True,
        "note": note,
        "tech_stack_top8_by_bytes": top_tech,
        "commit_cadence_52w": {
            "weekly_counts": cadence,
            "total_commits": sum(cadence),
            "weeks_with_activity": sum(1 for count in cadence if count > 0),
        },
        "active_job_snapshot": [
            {
                "title": item.get("title"),
                "location": item.get("location"),
            }
            for item in jobs[:30]
            if isinstance(item, dict)
        ],
        "blog_snapshot": [
            {"title": item.get("title")}
            for item in blog[:15]
            if isinstance(item, dict)
        ],
    }


def _to_jsonable_retrieval(retrieval: dict) -> dict:
    return {
        "queries_used": retrieval.get("queries_used", 0),
        "examples": [
            {
                **item,
                "collection_date": item["collection_date"].isoformat()
                if isinstance(item.get("collection_date"), date) else item.get("collection_date"),
            }
            for item in retrieval.get("examples", [])
        ],
        "github_groups": deepcopy(retrieval.get("github_groups", [])),
        "week_match_counts": dict(retrieval.get("week_match_counts", {})),
    }


def _shorten_payload(payload: dict) -> None:
    for item in payload["current_week_events"]:
        item["text"] = _truncate_words(item.get("text"), SHORT_TEXT_WORDS)
    retrieval = payload["retrieved_history"]
    for item in retrieval["examples"]:
        item["text"] = _truncate_words(item.get("text"), SHORT_TEXT_WORDS)
    for group in retrieval["github_groups"]:
        group["sample_texts"] = [
            _truncate_words(text, SHORT_TEXT_WORDS // 2)
            for text in group.get("sample_texts", [])[:3]
        ]
    baseline = payload["baseline_context"]
    baseline["active_job_snapshot"] = baseline.get("active_job_snapshot", [])[:10]
    baseline["blog_snapshot"] = baseline.get("blog_snapshot", [])[:8]


def _collapse_current_github(events: list[dict]) -> list[dict]:
    github: dict[str, list[dict]] = {}
    other = []
    for event in events:
        if event["source"] != "github":
            other.append(event)
            continue
        repo = event["text"].splitlines()[0].removeprefix("Repository: ") or "unknown"
        github.setdefault(repo, []).append(event)

    groups = []
    for repo, items in sorted(github.items()):
        groups.append({
            "source": "github",
            "repo": repo,
            "event_count": len(items),
            "event_ids": [item["event_id"] for item in items],
            "events": [
                {
                    "event_id": item["event_id"],
                    "collection_date": item["collection_date"],
                    "date_basis": item["date_basis"],
                    "title": item["title"],
                    "one_liner": item["one_liner"],
                    "text": item["text"],
                }
                for item in items
            ],
        })
    return other + groups


def _all_current_event_ids(events: list[dict]) -> set[int]:
    ids: set[int] = set()
    for item in events:
        if item.get("source") == "github" and "events" in item:
            ids.update(detail["event_id"] for detail in item["events"])
        else:
            ids.add(item["event_id"])
    return ids


def _compact_until_fit(payload: dict, config: WeeklyConfig) -> tuple[dict, int, list[str]]:
    result = deepcopy(payload)
    log: list[str] = []
    initial_ids = _all_current_event_ids(result["current_week_events"])

    def estimates() -> tuple[int, int]:
        return estimate_tokens(result), estimate_tokens(result["retrieved_history"])

    total, retrieval_tokens = estimates()
    retrieval_budget = math.floor(config.input_token_budget * config.retrieval_budget_share)
    if total <= config.input_token_budget and retrieval_tokens <= retrieval_budget:
        return result, total, log

    _shorten_payload(result)
    log.append("shorten_text")
    total, retrieval_tokens = estimates()
    if total <= config.input_token_budget and retrieval_tokens <= retrieval_budget:
        return result, total, log

    if any(event.get("source") == "github" for event in result["current_week_events"]):
        result["current_week_events"] = _collapse_current_github(result["current_week_events"])
        log.append("collapse_github_by_repo")
    total, retrieval_tokens = estimates()
    if total <= config.input_token_budget and retrieval_tokens <= retrieval_budget:
        return result, total, log

    retrieval = result["retrieved_history"]
    examples = retrieval["examples"]
    omitted = list(retrieval.get("omitted_example_event_ids", []))
    while examples and (total > config.input_token_budget or retrieval_tokens > retrieval_budget):
        keep_count = len(examples) // 2
        removed = examples[keep_count:]
        omitted.extend(item["event_id"] for item in removed)
        retrieval["examples"] = examples[:keep_count]
        examples = retrieval["examples"]
        retrieval["omitted_example_event_ids"] = sorted(set(omitted))
        log.append("reduce_retrieval_examples")
        total, retrieval_tokens = estimates()

    if retrieval_tokens > retrieval_budget:
        for group in retrieval["github_groups"]:
            group["sample_texts"] = []
        total, retrieval_tokens = estimates()
        if log[-1:] != ["reduce_retrieval_examples"]:
            log.append("remove_retrieval_samples")

    if total > config.input_token_budget or retrieval_tokens > retrieval_budget:
        raise WeeklyInputError("input_over_budget", total, config.input_token_budget)
    if _all_current_event_ids(result["current_week_events"]) != initial_ids:
        raise RuntimeError("Input compaction unexpectedly omitted current-week events.")
    return result, total, log


def build_weekly_input(
    db: Session,
    *,
    target_id: int,
    week_start: date,
    today: date | None = None,
    prompt_version: str = PROMPT_VERSION,
    config: WeeklyConfig = WEEKLY_CONFIG,
    retrieval_data: dict | None = None,
    as_of_date: date | None = None,
) -> dict:
    """Load weekly evidence and build a complete, budget-checked JSON payload."""
    require_monday(week_start)
    target = db.query(Target).filter_by(id=target_id).first()
    if target is None:
        raise ValueError("target_not_found")

    window = week_window(week_start)
    earliest = previous_week_starts(week_start, config.history_weeks)[0]
    events = db.query(Event).filter(
        Event.target_id == target_id,
        Event.is_baseline.is_(False),
        Event.collection_date >= earliest,
        Event.collection_date <= window.end_date,
    ).order_by(Event.collection_date.asc(), Event.id.asc()).all()
    summaries = db.query(DailySourceSummary).filter(
        DailySourceSummary.target_id == target_id,
        DailySourceSummary.collection_date >= week_start,
        DailySourceSummary.collection_date <= window.end_date,
    ).order_by(DailySourceSummary.collection_date.asc(), DailySourceSummary.id.asc()).all()
    kundali = db.query(Kundali).filter_by(target_id=target_id).first()
    if kundali is not None and as_of_date is not None and kundali.created_at is not None:
        created_at = kundali.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        snapshot_day = created_at.astimezone(ZoneInfo(config.timezone)).date()
        if snapshot_day > as_of_date:
            kundali = None

    states = source_coverage(
        target,
        events,
        summaries,
        week_start,
        today=today,
        unverified_threshold=config.unverified_threshold,
    )
    counts = weekly_event_counts(events, week_start, history_weeks=config.history_weeks)
    prior_starts = previous_week_starts(week_start, config.history_weeks)
    current_events = [
        _current_event(event)
        for event in events
        if week_start <= event.collection_date <= window.end_date
    ]
    history_weeks_available = len({
        event.collection_date - timedelta(days=event.collection_date.weekday())
        for event in events
        if event.collection_date < week_start
    })
    if retrieval_data is None:
        retrieval_data = retrieve_history(
            db,
            target_id=target_id,
            week_start=week_start,
            max_queries=config.max_queries,
            example_k=config.example_k,
            count_k=config.count_k,
            min_similarity=config.min_similarity,
        )

    weekly_counts = [
        {"week_start": start.isoformat(), "sources": counts[start]}
        for start in prior_starts + [week_start]
    ]
    source_rows = []
    for source in SOURCES:
        state = states[source]
        source_rows.append({
            "source": source,
            "configured": state.configured,
            "activity": state.activity,
            "coverage": state.coverage,
            "event_count": state.event_count,
            "expected_days": state.expected_days,
            "collected_days": state.collected_days,
            "missing_dates": [day.isoformat() for day in state.missing_dates],
            "candidates_total": state.candidates_total,
            "known_items": state.known_items,
            "failed_items": state.failed_items,
            "cap_skipped_items": state.cap_skipped_items,
            "unverified_share": state.unverified_share,
            "reasons": list(state.reasons),
        })

    payload = {
        "metadata": {
            "target_id": target_id,
            "week_start": window.start_date.isoformat(),
            "week_end": window.end_date.isoformat(),
            "timezone": config.timezone,
            "prompt_version": prompt_version,
            "history_weeks_available": history_weeks_available,
            "history_window_weeks": config.history_weeks,
        },
        "sources": source_rows,
        "weekly_counts": weekly_counts,
        "current_week_events": current_events,
        "retrieved_history": _to_jsonable_retrieval(retrieval_data),
        "baseline_context": _baseline_context(kundali),
    }
    fitted, token_estimate, compaction_log = _compact_until_fit(payload, config)
    fitted["metadata"]["compaction_level"] = "+".join(compaction_log) if compaction_log else "none"
    fitted["metadata"]["compaction_log"] = compaction_log
    fitted["metadata"]["estimated_tokens"] = 0
    token_estimate = estimate_tokens(fitted)
    fitted["metadata"]["estimated_tokens"] = token_estimate
    token_estimate = estimate_tokens(fitted)
    fitted["metadata"]["estimated_tokens"] = token_estimate
    if token_estimate > config.input_token_budget:
        raise WeeklyInputError("input_over_budget", token_estimate, config.input_token_budget)
    return fitted
