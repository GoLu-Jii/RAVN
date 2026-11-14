<img src="./diagrams/dfd_level-1.png" alt="DFD Level 1" width="550">

# RAVN — Component Specification

## 1. Auth Handler (Implemented)

- **Trigger:** Sign-up, login, or session validation on each request
- **Input:** User credentials or session token
- **Output:** Hashed/verified passwords, issued session tokens (JWT), validated tokens on protected routes, identified requesting user → scopes every downstream action to that user
- **Feeds:** User identity → Onboarding Handler and Dashboard/UI

---

## 2. Onboarding Handler (Implemented)

- **Trigger:** User submits a company website URL
- **Input:** Company website URL
- **Output:** Confirmed set of source URLs (GitHub org, ATS board, blog, web/social) → handed to Kundali builder; also saved as the target's known sources
- **Feeds:** Confirmed sources → Kundali Builder (background trigger) and Event Store (target registry entry)

---

## 3. Kundali Builder (Implemented)

- **Trigger:** Immediately after onboarding confirmation (runs in background or synchronously via `?sync=true`); target shows "building" in dashboard meanwhile.
- **Input:** Confirmed source URLs for that target (GitHub, ATS/careers, blog, web/social).
- **Process:**
  - Multi-track ingestion across GitHub (repos, commit activity, language byte-counts), ATS APIs / career pages (active job requisitions), technical blogs (RSS/Atom with `trafilatura` HTML article fallback), and web/social snapshots.
  - Multi-provider LLM synthesis pass: requests for `openai/gpt-oss-120b` route to Groq first, then Gemini, then OpenRouter, producing a structured competitive intelligence dossier (`executive_summary`, `strategic_focus_areas`, `cross_signal_correlations`, `predictive_outlook`).
- **Output:** The Day 0 profile — empirical signals (`tech_stack`, 52-week `cadence_baseline`, `recent_commits`, `ats_postings`, `blog_status`, `web_social_status`) and strategic intelligence dossier (`focus_areas`) persisted via idempotent DB upsert.
- **Feeds:** Finished Kundali profile &rarr; database; flips target status to "ready". Accessible via dedicated endpoints: `/targets/{id}/kundali/raw`, `/targets/{id}/kundali/synthesis`, and `/targets/{id}/kundali`.

---

## 4. Daily Collector (Implemented; automated tests added)

Automated `unittest` coverage includes date parsing, source-date boundaries, repeat-run deduplication,
baseline behavior, past-date collection, per-source date parsing, and startup catch-up.

- **Trigger:** Scheduled for 11:59 PM Asia/Kolkata once per day for ready targets, or manually via `POST /targets/{id}/daily/collect`; each source is handled independently.
- **Input:** Target's confirmed source URLs and an Asia/Kolkata calendar date (12:00 AM through 11:59 PM). Repeated manual runs use the same date and deduplicate stored events.
- **Output:** Source-dated commits, job posts, and blog posts; current-day web/social changes; and current-day undated ATS/blog items with first-observed dates. Events carry `date_basis` (`source` or `first_observed`); no LLM synthesis is run in this stage.
- **Feeds:** Dated events must fall on the requested Asia/Kolkata calendar date. The first successful current-day ATS/blog snapshot baselines genuinely undated listings; newly appearing undated listings on later runs are stored with their first-observed time. Known URLs, detail-fetch failures, and candidates beyond the lookup cap are `not_checked`, excluded from event storage and `undated_items`, and counted separately in the collection result. Past-date collection skips undated items and web/social with an explanatory status because those cannot be reconstructed. The daily summary stores per-source status, event count, genuinely undated-item count, and highlight. First-run web snapshots are also baseline.
- **Dates:** The shared `_parse_date` helper handles datetimes, date-only values, ISO strings (including `Z`), RFC-style strings, epoch seconds/milliseconds, and feedparser `struct_time`. Aware timestamps are assigned to the Asia/Kolkata calendar date; naive datetimes are treated as UTC; date-only values keep their date.
- **ATS/blog dates:** Greenhouse `updated_at` is only a candidate pre-filter; the individual job API's `first_published` determines the source date. The HTML ATS fallback reads JSON-LD `JobPosting.datePosted` from unseen job pages, capped at 20 detail lookups per source collection. Blog feeds fall back through `published`, `published_parsed`, `updated`, and `updated_parsed`; HTML article dates come from listing `<time>` metadata or trafilatura article metadata.
- **Highlights:** Each source/date highlight joins up to the three newest event one-liners and reports any remaining event count. It is formatting, not LLM-generated analysis.
- **History:** `GET /targets/{id}/daily/summaries` reads the four highlights for a date; `/daily/summaries/dates` lists dates with stored summary records. Historical baseline-only dates have `baseline_only` status; missing historical summaries report `not_recorded` because collection status cannot be inferred.
- **Current source-date limitation:** The configured ready targets currently resolve to one unique ATS and blog URL (Stripe). A live 2026-11-06 check found source dates for all 6 ATS fallback listings and all 5 blog entries returned in the sample; the previous 2026-11-05 run saw 5/5 ATS and 15/15 blog items without parsed dates before detail-page date extraction. Current-day first-observed dates mean when RAVN noticed an item, not when the employer published it. Past-date runs do not assign current observation times to historical days. These samples should not be generalized to other companies.
- **Tagging boundary:** Events currently receive fixed source/type labels only; semantic product/topic tagging is not implemented. The Weekly Lead Detector classifies evidence through the configured LLM and preserves event citations.
- **Recovery boundary:** After starting the 23:59 scheduler, the app checks each ready target in a background thread for the latest three completed dates with no daily summary row or with all four sources failed, then collects those gaps. Configure the count with `RAVN_DAILY_CATCHUP_DAYS` (default `3`). Catch-up and scheduled collections share a process-local lock. This is idempotent through daily summaries and event deduplication. Past-date catch-up can recover source-dated GitHub, ATS, and blog items; it skips web/social snapshots and undated first-observed items. It cannot recover source items that have since been removed, and multi-process scheduler ownership has not been configured.

---

## 5. Event Store (Implemented)

- **Trigger:** Passive; written by the Kundali builder and daily collector, read by APIs and weekly analysis.
- **Holds:** users, targets, Kundali snapshots, daily events, source checkpoints, daily source summaries, event embeddings, weekly lead runs/vectors, and separate signal runs/outputs.
- **Daily read API:** owner-scoped event feed/detail and four source/date summaries. Baseline events are hidden from the feed.
- **Weekly records:** `weekly_lead_runs` keeps lead-extraction attempts; `weekly_leads` stores validated leads, source event IDs, week date, and vector.
- **Signal records:** `signal_detection_runs` keeps signal attempts and evidence snapshots; `detected_signals` stores current/historical lead citations, types, confidence, and rationale.
- **Embeddings:** `event_embeddings` stores versioned 256-dimensional Voyage vectors. Embeddings are generated by the MongoDB Atlas-hosted Voyage API; PostgreSQL/pgvector in the existing Supabase database remains the vector store. SQLite uses JSON vectors for offline tests.

---

## 6. Weekly Lead Detector (Baseline Implemented)

The analysis week is Monday through Sunday by Asia/Kolkata `collection_date`. The scheduled job runs Monday at 02:00 Asia/Kolkata for the prior closed week. Manual runs use the same pipeline and can select a Monday start date.

- **Input:** All non-baseline events collected during the selected week, each with database ID, collection date, source, and `raw_content`.
- **Process:** Send the full weekly snapshot to the configured LLM without historical retrieval or compaction. Validate JSON shape and ensure every cited event ID belongs to the selected-week input. Embed only each lead's title and description using Voyage.
- **Output:** One vector row per lead, associated with target, Monday week date, and source event IDs. An empty week still calls the LLM and stores `{"leads": null}` without creating vectors; events with no supported leads use `{"leads": []}`.
- **Failure safety:** Every attempt is retained. Failed runs never replace the previous current lead set; a successful rerun atomically supersedes it.
- **API:** `POST /targets/{id}/weekly/run`, `GET /targets/{id}/weekly`, `GET /targets/{id}/weekly/{week_start}`, and `GET /targets/{id}/weekly/{week_start}/runs` (all owner-scoped).
- **Boundary:** The Weekly Lead Detector does not compare weeks or include the Kundali. The daily collection and event-embedding path is unchanged.
- **Scheduling:** Monday 02:00 Asia/Kolkata with one retry for retryable failures; manual runs remain enabled for development. Scheduler and run lock are process-local.

---
## 7. Signal Detector (Baseline Implemented)

The Signal Detector runs after the weekly lead stage and operates on saved lead vectors, not raw daily events.

- **Trigger:** Monday 04:00 Asia/Kolkata; manual owner-scoped run is available.
- **Handoff:** Requires the current successful weekly run. If it is absent or running, checks repeat at 30-minute intervals for three total checks. The first retryable weekly failure may receive one retry; a second failure skips signal detection.
- **Input:** Current Kundali baseline, all current-week leads, and up to three similar leads per current lead from the prior 12 weeks for the same target. Candidate matches include their dates, similarity, and retrieval context.
- **Budget:** Preserve the Kundali and current leads. Trim historical candidates oldest-first to stay within the configured default 49,000-byte conservative byte ceiling (a conservative proxy for the below-50,000-token cap); fail if the required baseline/current evidence alone is too large.
- **Output:** Typed signals with qualitative confidence/rationale and validated current/historical lead citations. Historical pattern types require historical citations. Empty current weeks still call the LLM with empty current/history lists.
- **Storage:** Separate `signal_detection_runs` and `detected_signals` tables. Failed attempts preserve the previous current signal set; successful reruns supersede it without deleting history.
- **API:** `POST /targets/{id}/weekly/signals/run`, `GET /targets/{id}/weekly/{week_start}/signals`, and `GET /targets/{id}/weekly/{week_start}/signals/runs` (owner-scoped).

---
## 8. Weekly Narrator (Multi-Agent Loop; Planned)

- **Trigger:** Signal Detector completes, always
- **Input:** Candidate signals + their underlying raw event data
- **Output:** The written executive digest (plain-language explanation of what happened and why it matters)
- **Process:**
  - Signal Analyst — selects which candidate signals are genuinely worth reporting
  - Verifier — independently checks each against raw underlying data, looking for disconfirming evidence; can reject/downgrade and send back to Analyst
  - Writer — drafts the plain-language narrative from verified signals only (or the low-activity summary)
  - Critic — checks the draft against verified facts for overstatement; can send back to Writer with notes
- **Feeds:** Finalized digest → Event Store, PDF Exporter, Dashboard

---

## 9. PDF Exporter (Planned)

- **Trigger:** User requests export (on-demand only, not auto-run weekly)
- **Input:** The written digest from #7
- **Output:** A downloadable PDF file

---

## 10. Dashboard/UI (Planned)

- **Trigger:** User opens the app
- **Input:** One-liners from daily collector, full detail from event store (on drilldown), digests from weekly narrator — all scoped to the logged-in user's own targets via Auth Handler
- **Output:** Nothing downstream — this is the terminal, user-facing display layer
- **Features:** Shows one-liner daily feed (with drilldown to full raw event detail), target status/Kundali profile, weekly digests, and export action

---

## Additional Product Decisions

- **Target Deletion:** Planned full purge — all target data removed from the database. No target-delete endpoint is implemented yet.
- **PDF Export:** On-demand only, generated when the user requests it.

---
