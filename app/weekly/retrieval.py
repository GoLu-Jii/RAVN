"""Target-scoped, exact similarity retrieval for prior weekly events."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from math import sqrt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.embeddings.model import EMBEDDING_DIMENSION
from app.embeddings.text import build_embedding_text
from app.events.models import Event, EventEmbedding
from app.weekly.config import WEEKLY_CONFIG
from app.weekly.windows import previous_week_starts, require_monday


@dataclass(frozen=True)
class _Query:
    event_id: int
    source: str
    repo: str | None
    vector: list[float]


def _repo(event: Event) -> str:
    import json

    try:
        payload = json.loads(event.raw_content or "")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return str(payload.get("repo") or event.title.removeprefix("Commit in "))


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Query and stored embedding dimensions differ.")
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def search_similar_events(
    db: Session,
    query_vector: list[float],
    *,
    target_id: int,
    week_start: date,
    limit: int,
    model_id: str | None = None,
    prep_version: str | None = None,
    backend: str | None = None,
) -> list[dict]:
    """Return exact cosine-ranked non-baseline events from prior history only."""
    require_monday(week_start)
    if len(query_vector) != EMBEDDING_DIMENSION:
        raise ValueError(f"Expected {EMBEDDING_DIMENSION}-dimensional query vector.")
    if limit < 1:
        return []
    config = WEEKLY_CONFIG
    model_id = model_id or config.embed_model_id
    prep_version = prep_version or config.prep_version
    lower_date = previous_week_starts(week_start, config.history_weeks)[0]
    selected_backend = backend or db.get_bind().dialect.name
    if selected_backend not in {"postgresql", "sqlite", "python"}:
        raise ValueError(f"Unsupported retrieval backend: {selected_backend}")

    filters = (
        Event.target_id == target_id,
        Event.is_baseline.is_(False),
        Event.collection_date >= lower_date,
        Event.collection_date < week_start,
        EventEmbedding.model_id == model_id,
        EventEmbedding.prep_version == prep_version,
        EventEmbedding.dimension == EMBEDDING_DIMENSION,
    )
    statement = select(Event, EventEmbedding).join(
        EventEmbedding, EventEmbedding.event_id == Event.id
    ).where(*filters)

    if selected_backend == "postgresql":
        distance = EventEmbedding.embedding.cosine_distance(query_vector)
        rows = db.execute(
            select(Event, EventEmbedding, distance.label("distance"))
            .join(EventEmbedding, EventEmbedding.event_id == Event.id)
            .where(*filters)
            .order_by(distance.asc(), Event.id.asc())
            .limit(limit)
        ).all()
        ranked = [
            (event, embedding, 1.0 - float(distance_value))
            for event, embedding, distance_value in rows
        ]
    else:
        rows = db.execute(statement).all()
        ranked = [
            (event, embedding, _cosine_similarity(query_vector, embedding.embedding))
            for event, embedding in rows
        ]
        ranked.sort(key=lambda item: (-item[2], item[0].id))
        ranked = ranked[:limit]

    return [
        {
            "event_id": event.id,
            "source": event.source_type,
            "collection_date": event.collection_date,
            "date_basis": event.date_basis or "source",
            "similarity": similarity,
            "text": build_embedding_text(event),
            "repo": _repo(event) if event.source_type == "github" else None,
        }
        for event, _embedding, similarity in ranked
    ]


def _current_queries(
    db: Session,
    target_id: int,
    week_start: date,
    *,
    model_id: str,
    prep_version: str,
    max_queries: int,
) -> list[_Query]:
    end_date = week_start.fromordinal(week_start.toordinal() + 6)
    rows = db.execute(
        select(Event, EventEmbedding)
        .join(EventEmbedding, EventEmbedding.event_id == Event.id)
        .where(
            Event.target_id == target_id,
            Event.is_baseline.is_(False),
            Event.collection_date >= week_start,
            Event.collection_date <= end_date,
            EventEmbedding.model_id == model_id,
            EventEmbedding.prep_version == prep_version,
            EventEmbedding.dimension == EMBEDDING_DIMENSION,
        )
        .order_by(Event.collection_date.asc(), Event.id.asc())
    ).all()

    selected: list[_Query] = []
    seen_repos: set[str] = set()
    for event, embedding in rows:
        repo = _repo(event) if event.source_type == "github" else None
        if repo is not None:
            if repo in seen_repos:
                continue
            seen_repos.add(repo)
        selected.append(_Query(event.id, event.source_type, repo, list(embedding.embedding)))
    return selected[:max_queries]


def retrieve_history(
    db: Session,
    *,
    target_id: int,
    week_start: date,
    model_id: str | None = None,
    prep_version: str | None = None,
    max_queries: int | None = None,
    example_k: int | None = None,
    count_k: int | None = None,
    min_similarity: float | None = None,
    backend: str | None = None,
) -> dict:
    """Build merged examples, match counts, age weights, and GitHub weekly groups."""
    require_monday(week_start)
    config = WEEKLY_CONFIG
    model_id = model_id or config.embed_model_id
    prep_version = prep_version or config.prep_version
    max_queries = config.max_queries if max_queries is None else max_queries
    example_k = config.example_k if example_k is None else example_k
    count_k = config.count_k if count_k is None else count_k
    if min_similarity is None:
        min_similarity = config.min_similarity
    if min(max_queries, example_k, count_k) < 1:
        raise ValueError("max_queries, example_k, and count_k must be at least 1.")

    queries = _current_queries(
        db,
        target_id,
        week_start,
        model_id=model_id,
        prep_version=prep_version,
        max_queries=max_queries,
    )
    merged: dict[int, dict] = {}
    example_ids: set[int] = set()

    for query in queries:
        hits = search_similar_events(
            db,
            query.vector,
            target_id=target_id,
            week_start=week_start,
            limit=count_k,
            model_id=model_id,
            prep_version=prep_version,
            backend=backend,
        )
        eligible = [
            hit for hit in hits
            if min_similarity is None or hit["similarity"] >= min_similarity
        ]
        for hit in eligible:
            item = merged.get(hit["event_id"])
            if item is None:
                item = {**hit, "query_event_ids": [], "sample_texts": []}
                merged[hit["event_id"]] = item
            item["query_event_ids"].append(query.event_id)
        for hit in eligible[:example_k]:
            example_ids.add(hit["event_id"])

    prior_weeks = previous_week_starts(week_start, config.history_weeks)
    week_match_counts = {start.isoformat(): 0 for start in prior_weeks}
    github_groups: dict[tuple[str, date], list[dict]] = defaultdict(list)
    examples = []

    def age_fields(item: dict) -> dict:
        event_week = item["collection_date"] - timedelta(days=item["collection_date"].weekday())
        age_weeks = (week_start - event_week).days // 7
        return {
            "age_weeks": age_weeks,
            "age_weight": 0.5 ** (age_weeks / config.half_life_weeks),
        }

    for event_id, item in merged.items():
        item["query_event_ids"] = sorted(set(item["query_event_ids"]))
        item.update(age_fields(item))
        event_week_start = item["collection_date"] - timedelta(days=item["collection_date"].weekday())
        week_key = event_week_start.isoformat()
        week_match_counts[week_key] += 1
        if event_id in example_ids:
            examples.append(item)
        if item["source"] == "github":
            github_groups[(item["repo"] or "unknown", event_week_start)].append(item)

    examples.sort(key=lambda item: (-item["similarity"], item["event_id"]))
    collapsed_github = []
    for (repo, start), items in sorted(github_groups.items(), key=lambda pair: (pair[0][1], pair[0][0])):
        items.sort(key=lambda item: (-item["similarity"], item["event_id"]))
        collapsed_github.append({
            "repo": repo,
            "week_start": start.isoformat(),
            "count": len(items),
            "event_ids": [item["event_id"] for item in items],
            "sample_texts": [item["text"] for item in items[:3]],
            "max_similarity": items[0]["similarity"],
            "age_weeks": items[0]["age_weeks"],
            "age_weight": items[0]["age_weight"],
        })

    return {
        "target_id": target_id,
        "week_start": week_start.isoformat(),
        "queries_used": len(queries),
        "examples": examples,
        "github_groups": collapsed_github,
        "week_match_counts": week_match_counts,
    }
