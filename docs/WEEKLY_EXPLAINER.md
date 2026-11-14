# Weekly Lead Detector: plain-language notes

The Weekly Lead Detector and baseline Signal Detector are implemented as two separate stages. Weekly processing turns one target's daily event records for one Monday–Sunday collection week into evidence-backed leads and stores each lead with a vector and date metadata. Signal processing compares those leads with the Kundali baseline and related leads from prior weeks.

## What goes into a weekly run

The detector selects rows from `events` where:

```text
target_id = requested target
collection_date >= selected Monday
collection_date < following Monday
is_baseline = false
```

The query uses `collection_date`—the Asia/Kolkata date RAVN saw and stored the event—not the original source publication date. `event_date` and `date_basis` remain available in the event record but do not determine week membership.

Every selected event is sent as an object containing:

```json
{
  "id": 123,
  "collection_date": "2026-11-09",
  "source": "github",
  "raw_content": "..."
}
```

The full input is saved on the run so it can be inspected later. There is no weekly compaction, chunking, or cache in this baseline. An oversized input must fail visibly rather than omit events.

## LLM response

The configured weekly model receives the weekly input and is asked to return:

```json
{
  "leads": [
    {
      "title": "Company is expanding its data infrastructure team",
      "description": "Two new roles focus on streaming data and large-scale storage.",
      "source_event_ids": [124, 128]
    }
  ]
}
```

Each lead must have a non-empty title, a non-empty description, and one or more distinct event IDs present in the selected-week input. The response is rejected if its JSON shape is invalid or if a lead cites any event outside this input.

For an empty week, the LLM is still called and must return:

```json
{"leads": null}
```

The run status is `no_events`; no lead rows or vectors are created. If events exist but the LLM finds no supported leads, it returns `{"leads":[]}` and the run status is `succeeded`.

## Embeddings and persistence

The hosted Voyage model embeds only:

```text
{title}
{description}
```

The title and description are separated by a newline. Week dates, target IDs, run IDs, and source-event IDs are structured metadata and are not embedded.

The additive migration creates:

- `weekly_lead_runs`: an attempt record, exact input event IDs and JSON snapshot, LLM output, status, model metadata, and failure details.
- `weekly_leads`: one row per lead, including its text, cited event IDs, target ID, Monday `week_start_date`, embedding model, and 256-dimensional vector.

PostgreSQL stores vectors as pgvector `vector(256)`; SQLite uses JSON-backed vectors for development and tests. The application stores vectors in the configured RAVN database; the Voyage API only computes them.

Every attempt is retained. A failed run does not change the current successful lead set. Lead rows and a successful run are committed in one transaction; a successful rerun marks the previous current run as superseded and becomes current. Earlier successful data remains available through its run history.

## Schedule, retries, and API

- The scheduled job runs Monday at 02:00 Asia/Kolkata for the last fully closed week.
- One retry is scheduled after a retryable failure.
- The owner-scoped manual run endpoint remains enabled for development and can select a Monday date.
- Startup recovery attempts the last closed week even if no daily events were collected, preserving the empty-week behavior.
- The APScheduler and overlap lock are process-local; they do not coordinate multiple application instances.

Owner-scoped endpoints:

- `POST /targets/{target_id}/weekly/run?week_start=YYYY-MM-DD` — run one Monday-start week; defaults to the last closed week.
- `GET /targets/{target_id}/weekly` — list each week's current run and leads.
- `GET /targets/{target_id}/weekly/{week_start}` — retrieve one week's current run and leads.
- `GET /targets/{target_id}/weekly/{week_start}/runs?include_input=true` — inspect attempt history; the full input and output are optional.

## Signal Detector

The Signal Detector runs Monday at 04:00 Asia/Kolkata, after the 02:00 weekly lead run. A manual owner-scoped endpoint is available for testing. It requires a current weekly run in `succeeded` or `no_events` state; it never creates or modifies weekly leads.

For each current-week lead vector, the detector searches the previous 12 weeks for the same target and returns up to three nearest lead vectors. PostgreSQL uses pgvector cosine distance; SQLite tests rank cosine similarity in Python. Only leads belonging to the current successful lead run for each past week are eligible. Historical candidates are deduplicated across current leads and sent with their IDs, Monday dates, similarity, and retrieval context.

The LLM receives:

- The target's current Kundali fields as background context.
- Every current-week lead with its lead ID and source-event IDs.
- Selected historical lead candidates with IDs, week dates, similarities, and the current leads that retrieved them.

When there are no current leads, the LLM is still called with the Kundali and empty current/history lists. The output uses this shape:

```json
{
  "signals": [
    {
      "title": "Expansion of real-time data capabilities",
      "description": "Current hiring and engineering activity indicate investment in real-time data infrastructure.",
      "signal_types": ["emerging", "cross_source_pattern"],
      "confidence": "medium",
      "confidence_rationale": "Current evidence agrees, but no earlier weekly lead confirms duration.",
      "current_lead_ids": [501, 508],
      "historical_lead_ids": []
    }
  ],
  "no_matches_reason": null
}
```

Every signal must cite a current lead. `continuation`, `acceleration`, `deceleration`, and `strategic_shift` require historical lead citations. All IDs are checked against the exact current leads and selected RAG candidates. Similarity is candidate relevance, not proof; the LLM may reject retrieved leads. Confidence is qualitative, not a calibrated probability.

The Signal Detector preserves Kundali and current leads and caps the system prompt plus serialized input at a default conservative 49,000-byte bound, used as a proxy for the below-50,000-token cap. If needed, it removes older historical candidates first. If the required baseline/current evidence alone exceeds the limit, the attempt fails rather than dropping it.

`signal_detection_runs` records each attempt and evidence snapshot. `detected_signals` stores each validated output and cited IDs. Failed attempts preserve the previous current signal set; successful reruns supersede it while keeping history.

If weekly processing is absent or still running at 04:00, the scheduler retries the handoff at 04:30 and 05:00. A first retryable weekly failure may receive one retry; a subsequent failure skips signal detection. The signal stage does not automatically retry its own failed analysis. Both APIs and stored results are target-owner scoped.

Owner-scoped endpoints:

- `POST /targets/{target_id}/weekly/signals/run?week_start=YYYY-MM-DD` — manually run a week after successful weekly lead generation.
- `GET /targets/{target_id}/weekly/{week_start}/signals` — read the current signal set.
- `GET /targets/{target_id}/weekly/{week_start}/signals/runs?include_input=true` — inspect attempt history and optional input/output.

## Deliberate boundaries

The Weekly Lead Detector does not query previous-week events or event embeddings, include the Kundali, or generate/score signals; those are performed by the separate Signal Detector. The Signal Detector does not modify weekly leads or raw daily events. Neither stage changes the daily collector's existing event-embedding path.

Legacy signal-analysis helpers, replay scripts, and `weekly_runs`/`weekly_results`/`weekly_signals` tables remain in the repository for compatibility and offline evaluation, but the active lead endpoint and scheduler no longer invoke or write them.
