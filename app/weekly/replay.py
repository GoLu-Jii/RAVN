"""Non-persisting historical replay and Markdown evaluation report."""
from collections import Counter
from datetime import date, datetime, timedelta
import json
import math
from statistics import median

from sqlalchemy.orm import Session

from app.embeddings.service import embed_missing_events
from app.events.models import Event
from app.targets.models import Target
from app.weekly.config import WEEKLY_CONFIG, WeeklyConfig
from app.weekly.input_builder import build_weekly_input
from app.weekly.llm_stage import run_weekly_llm
from app.weekly.prompt import PROMPT_VERSION
from app.weekly.scoring import enrich_verified_signal
from app.weekly.verify import verify_signals
from app.weekly.windows import last_closed_week, previous_week_starts, require_monday, week_window


class FakeReplayClient:
    """Predictable no-signal client; verifies prompt/parse flow without provider usage."""
    def __init__(self):
        self.chat = self
        self.completions = self

    def create(self, **_kwargs):
        output = {
            "summary": "Replay fake model: no signal judgments were generated.",
            "data_limitations": ["Fake replay mode does not evaluate signal quality."],
            "signals": [],
        }
        return type("FakeResponse", (), {
            "content": json.dumps(output),
            "provider": "fake",
            "model": "fake-replay",
            "raw_response": {"choices": [{"finish_reason": "stop"}], "usage": None},
        })()


def _percentile_10(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.10) - 1)
    return ordered[index]


def replay_weeks(
    db: Session,
    *,
    target_id: int,
    weeks: int,
    live: bool = False,
    now: datetime | None = None,
    config: WeeklyConfig = WEEKLY_CONFIG,
    llm_client=None,
    embedder=None,
) -> dict:
    """Replay the last N closed weeks without writing weekly runs/results."""
    if weeks < 1:
        raise ValueError("weeks must be at least one.")
    latest_closed = last_closed_week(now)
    week_starts = previous_week_starts(latest_closed, weeks - 1) + [latest_closed]

    target = db.query(Target).filter_by(id=target_id).first()
    if target is None:
        raise ValueError("target_not_found")
    embedding_report = embed_missing_events(db, target_id=target_id, embedder=embedder)
    if embedding_report["status"] in {"failed", "partial"}:
        raise RuntimeError(f"Embedding backfill failed: {embedding_report.get('error_type', 'unknown error')}")

    if not live and llm_client is None:
        llm_client = FakeReplayClient()

    replayed = []
    for week_start in week_starts:
        require_monday(week_start)
        week_end = week_window(week_start).end_date
        weekly_input = build_weekly_input(
            db,
            target_id=target_id,
            week_start=week_start,
            today=week_end + timedelta(days=1),
            as_of_date=week_end,
            config=config,
        )
        if weekly_input["current_week_events"]:
            llm_result = run_weekly_llm(
                weekly_input,
                client=llm_client,
                config=config,
                prompt_version=PROMPT_VERSION,
            )
        else:
            llm_result = {
                "output": {"summary": "Low activity; no current-week events.", "data_limitations": [], "signals": []},
                "raw_output": "",
                "provider": "none",
                "model": None,
                "prompt_version": PROMPT_VERSION,
                "usage": None,
                "attempts": 0,
            }

        persisted_events = db.query(Event).filter(
            Event.target_id == target_id,
            Event.collection_date >= week_start - timedelta(weeks=config.history_weeks),
            Event.collection_date <= week_end,
        ).all()
        verification = verify_signals(
            llm_result["output"]["signals"],
            persisted_events,
            target_id=target_id,
            week_start=week_start,
            history_weeks=config.history_weeks,
        )
        source_statuses = {
            row["source"]: {"configured": row["configured"], "coverage": row["coverage"]}
            for row in weekly_input["sources"]
        }
        signals = [
            enrich_verified_signal(
                item,
                source_statuses,
                week_start=week_start,
                trend_min_weeks=config.trend_min_weeks,
            )
            for item in verification["verified_signals"]
        ]
        proposed_ids = {
            event_id
            for candidate in llm_result["output"]["signals"]
            for event_id in candidate["cited_event_ids"]
        }
        verified_ids = {
            event_id
            for signal in verification["verified_signals"]
            for event_id in signal["cited_event_ids"]
        }
        similarities = [
            float(item["similarity"])
            for item in weekly_input["retrieved_history"]["examples"]
        ]
        rejection_reasons = Counter(
            reason
            for rejected in verification["rejected_signals"]
            for reason in rejected["rejection_reasons"]
        )
        replayed.append({
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "status": "low_activity" if not weekly_input["current_week_events"] else "replayed",
            "provider": llm_result["provider"],
            "model": llm_result["model"],
            "current_event_count": len(weekly_input["current_week_events"]),
            "history_weeks_available": weekly_input["metadata"]["history_weeks_available"],
            "signal_count": len(signals),
            "proposed_citation_count": len(proposed_ids),
            "verified_citation_count": len(verified_ids),
            "citations_pass_rate": len(verified_ids) / len(proposed_ids) if proposed_ids else None,
            "rejection_reasons": dict(rejection_reasons),
            "scores": [signal["score"] for signal in signals],
            "input_tokens": weekly_input["metadata"]["estimated_tokens"],
            "provider_tokens": (llm_result.get("usage") or {}).get("total_tokens"),
            "retrieval_similarities": similarities,
            "summary": llm_result["output"]["summary"],
            "input": weekly_input,
        })

    return {
        "target_id": target_id,
        "live": live,
        "embedding_status": embedding_report["status"],
        "weeks": replayed,
    }


def render_replay_report(replay: dict) -> str:
    similarity_values = [
        value
        for week in replay["weeks"]
        for value in week["retrieval_similarities"]
    ]
    suggested = _percentile_10(similarity_values)
    lines = [
        f"# RAVN weekly replay: target {replay['target_id']}",
        "",
        f"Mode: {'live LLM' if replay['live'] else 'fake LLM'}  ",
        f"Embedding status: `{replay['embedding_status']}`",
        "",
        f"Suggested `min_similarity` from the 10th percentile: `{suggested:.4f}` (suggestion only; not applied)."
        if suggested is not None else "Suggested `min_similarity`: unavailable (no retrieval hits).",
        "",
    ]
    for week in replay["weeks"]:
        rate = week["citations_pass_rate"]
        pass_text = f"{rate:.1%}" if rate is not None else "N/A (no citations proposed)"
        sims = week["retrieval_similarities"]
        if sims:
            similarity_text = (
                f"n={len(sims)}, min={min(sims):.4f}, median={median(sims):.4f}, max={max(sims):.4f}"
            )
        else:
            similarity_text = "no retrieval examples"
        lines.extend([
            f"## Week {week['week_start']} to {week['week_end']}",
            "",
            f"- Status: `{week['status']}`; model: `{week['model'] or 'none'}` ({week['provider']})",
            f"- Current events: {week['current_event_count']}; history weeks with events: {week['history_weeks_available']}",
            f"- Signals: {week['signal_count']}; citation pass rate: {pass_text}",
            f"- Rejections: `{json.dumps(week['rejection_reasons'], sort_keys=True)}`",
            f"- Scores: `{week['scores']}`",
            f"- Tokens: input estimate {week['input_tokens']}; provider total {week['provider_tokens'] or 'n/a'}",
            f"- Retrieval similarities: {similarity_text}",
            f"- Summary: {week['summary']}",
            "",
        ])
    return "\n".join(lines)
