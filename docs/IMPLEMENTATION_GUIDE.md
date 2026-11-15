# RAVN — Implementation Guide

This guide describes the current backend implementation. The Weekly Lead Detector and baseline Signal Detector are implemented; the frontend/dashboard, LangGraph narrator, and PDF export remain planned.

## Repository Structure

```text
app/
  auth/          authentication routes, user model, password/JWT helpers
  targets/       onboarding, source discovery, target routes/model
  kundali/       Day 0 collection, profile model, LLM dossier synthesis
  daily/         daily collector and GitHub/ATS/blog/web-social fetchers
  events/        event/source-summary models and daily APIs
  embeddings/    hosted embedding client, text preparation, persistence service
  weekly/        collection-week lead extraction, citation validation,
                 lead vector persistence, legacy signal helpers, routes
  scheduler/     daily and weekly APScheduler jobs plus recovery/retries
  db/            SQLAlchemy engine/session and declarative base
  llm/           existing multi-provider LLM client
  export/        planned PDF export
alembic/         database migration history
scripts/         migration, embedding backfill, input inspection, weekly replay
 tests/          offline unittest suites and opt-in integration checks
```

## Technology and Runtime

| Layer | Current choice |
|---|---|
| Language/API | Python 3.11, FastAPI |
| Persistence | SQLAlchemy, Alembic; SQLite supported, current deployment uses the existing Supabase PostgreSQL database |
| Vector search | pgvector on PostgreSQL; exact Python cosine search for SQLite/tests |
| Embeddings | MongoDB Atlas-hosted Voyage `voyage-4-lite`, 256 dimensions, via the REST API |
| Weekly LLM | Pinned model through OpenRouter by default; provider fallback disabled for detector calls |
| Other LLM calls | Kundali synthesis and onboarding use the existing multi-provider routing/fallback behavior |
| Scheduling | In-process APScheduler with Asia/Kolkata timezone |
| Collection | httpx, BeautifulSoup, feedparser, trafilatura; source-specific public APIs where available |
| Tests | Python `unittest`; offline tests use fakes, optional live database/model checks are opt-in |

The project owner uses an existing free-tier Supabase project because Docker is unavailable. Voyage supplies hosted embedding inference; Supabase remains the relational/vector database. `DATABASE_URL` selects Supabase for the app and Alembic; never commit the URL. SQLite remains usable for local development and all unit tests. Voyage's current free allocation is quota-limited and shared at the Atlas organization level; usage beyond the remaining free allocation may be billed. The scheduler uses an in-memory job store and a process-local lock, so only one application process should own scheduled jobs unless deployment-level coordination is added.

## Configuration

Generic settings include `DATABASE_URL`, `JWT_SECRET_KEY`, optional `GITHUB_TOKEN`, `OPENROUTER_API_KEY` for active detector calls, and provider keys such as `GROQ_API_KEY` for Kundali synthesis. Weekly settings are read from environment variables named `RAVN_WEEKLY_<FIELD>`; defaults are defined in `app/weekly/config.py`.

The Voyage client is paced for the free tier without a payment method: at most 3 requests and 10,000 estimated tokens per rolling minute. It splits inputs into requests capped at 8,000 estimated tokens and 64 items, then adjusts reservations using provider-reported token usage when available. Estimation uses UTF-8 bytes plus fixed overhead instead of a local tokenizer, so it is approximate. Pacing is process-local; multiple application processes or other clients using the same API key are not coordinated. Persistent HTTP 429 responses can indicate account quota exhaustion or a different organization limit; check the Atlas dashboard.

| Variable | Default | Purpose |
|---|---:|---|
| `RAVN_WEEKLY_WEEK_START` | `monday` | Fixed Monday week boundary |
| `RAVN_WEEKLY_SCHEDULE_HOUR` / `MINUTE` | `2` / `0` | Monday run time in `Asia/Kolkata` |
| `RAVN_WEEKLY_SIGNAL_SCHEDULE_HOUR` | `4` | Monday Signal Detector start time in `Asia/Kolkata` |
| `RAVN_WEEKLY_WEEKLY_INPUT_BYTE_BUDGET` | `48000` | Per-request weekly LLM input ceiling including prompt and overhead |
| `RAVN_WEEKLY_SIGNAL_INPUT_BYTE_BUDGET` | `49000` | Conservative UTF-8 byte ceiling; must remain below 50,000 |
| `RAVN_WEEKLY_TIMEZONE` | `Asia/Kolkata` | Calendar and scheduler timezone |
| `RAVN_WEEKLY_HISTORY_WEEKS` | `12` | Historical retrieval/count window |
| `RAVN_WEEKLY_HALF_LIFE_WEEKS` | `2.0` | Reported age weight; does not change retrieval ranking |
| `RAVN_WEEKLY_MAX_QUERIES` | `40` | Current-week query cap |
| `RAVN_WEEKLY_EXAMPLE_K` / `COUNT_K` | `5` / `50` | Similar examples and per-query match-count limits |
| `RAVN_WEEKLY_MIN_SIMILARITY` | unset | Optional similarity cutoff |
| `RAVN_WEEKLY_INPUT_TOKEN_BUDGET` | `12000` | Estimated input budget |
| `RAVN_WEEKLY_RETRIEVAL_BUDGET_SHARE` | `0.5` | Portion of input budget reserved for retrieval |
| `RAVN_WEEKLY_MAX_OUTPUT_TOKENS` / `MAX_SIGNALS` | `4000` / `8` | LLM response bounds |
| `RAVN_WEEKLY_UNVERIFIED_THRESHOLD` | `0.20` | ATS/blog missed-candidate share marking coverage partial |
| `RAVN_WEEKLY_TREND_MIN_WEEKS` | `3` | Distinct citation weeks required for a trend label |
| `RAVN_WEEKLY_WEEKLY_LLM_PROVIDER` | `openrouter` | Explicit provider for active weekly lead and signal extraction |
| `RAVN_WEEKLY_WEEKLY_LLM_MODEL` | `openai/gpt-oss-120b` | Pinned model requested from the selected provider |
| `RAVN_WEEKLY_EMBED_MODEL_ID` | `voyage-4-lite` | Voyage model identifier |
| `RAVN_WEEKLY_PREP_VERSION` | `voyage-document-v1` | Version of source text and document-mode embedding preparation |
| `VOYAGE_API_KEY` | required for embeddings | MongoDB Atlas model API key, sent as a Bearer token |
| `VOYAGE_EMBEDDING_PROVIDER_ENDPOINT` | `https://ai.mongodb.com/v1/embeddings` | Optional Atlas endpoint override; existing `ENDPOINT` is accepted as an alias |
| `RAVN_WEEKLY_RETRY_ATTEMPTS` | `1` | Scheduled retries after retryable failure |

The environment variable prefix is `RAVN_WEEKLY_` followed by the configuration field name uppercased (for example `RAVN_WEEKLY_WEEKLY_LLM_MODEL`). Active weekly lead and signal extraction explicitly use the configured provider (default `openrouter`) with fallback disabled. `OPENROUTER_API_KEY` is required for those calls; `GROQ_API_KEY` remains used by Kundali synthesis. `MONGODB_URI`, `MONGODB_USERNAME`, and `MONGODB_PASSWORD` are not used for embedding calls; the model API key authenticates directly with Atlas's embeddings endpoint. `DATABASE_URL` continues to point to Supabase. The replay command uses a fake no-signal LLM by default, but still calls Voyage when it needs to create embeddings; `--live` uses its legacy configured provider flow.

The history, retrieval, coverage, compaction, and signal-scoring settings in the table are retained for legacy weekly-analysis helpers and replay tooling. They do not affect the active Weekly Lead Detector. The Signal Detector uses its own fixed 12-week retrieval horizon and top-three-per-current-lead rule, plus `signal_input_byte_budget`.

Before PostgreSQL daily event-embedding work, RAVN checks that `event_embeddings.embedding` is `vector(256)`. If the table or dimension does not match, embedding stops before calling Voyage and the daily embedding service reports a non-retryable, actionable schema error. Apply `python -m alembic upgrade head` to the same database selected by `DATABASE_URL`; the 384-to-256 migration removes incompatible derived vectors but preserves event records. Missing event vectors are recreated through the hosted Voyage API as needed. Weekly lead vectors use the separate `weekly_leads.embedding` column. SQLite tests skip this PostgreSQL-specific preflight.

Voyage requests authenticate with `Authorization: Bearer <VOYAGE_API_KEY>`. Provider errors are returned without response bodies or credentials: 401/403 indicate a key or permission issue, 429 indicates a possible rate/quota limit, and network/429/5xx errors are retryable.

## Daily Collection

`POST /targets/{id}/daily/collect` collects all configured sources for the current Asia/Kolkata day. The scheduler runs at 23:59. Dated GitHub commits, job listings, and blog posts are assigned by their source date after timezone normalization. On current-day runs, the first ATS/blog listing snapshot is baseline; later genuinely undated new listings can be stored with `date_basis=first_observed`. Known URLs, failed detail requests, and cap overflow are not stored as undated items. Past-date collection skips web/social snapshots and first-observed items. Daily collection does not call an LLM.

Daily summaries store one row per target/date/source and include source status, event count, highlight, undated count, and candidate-coverage counters. The event feed excludes baseline rows and raw content; the event detail endpoint includes raw content. The four source highlights are available by collection date.

After the scheduler starts, startup catch-up runs in a background thread for recent dates missing summaries or with all four sources failed, bounded by `RAVN_DAILY_CATCHUP_DAYS` (default 3). It can recover dated sources only; web/social snapshots and first-observed events cannot be reconstructed for past dates.

GitHub daily collection currently stores commits. Pull requests and dependency-change events remain in the broader product scope and are not implemented.

## Weekly Lead Detector

The Weekly Lead Detector handles one target and one Monday–Sunday collection week. It runs on Monday at 02:00 Asia/Kolkata for the last closed week; the owner-scoped manual endpoint remains available for testing. It selects `events` by `collection_date >= Monday AND collection_date < next Monday` and excludes `is_baseline=true`. It does not retrieve prior weeks or include the Kundali.

1. Save a run attempt and snapshot the selected event IDs, collection dates, source names, and `raw_content`.
2. Send every event in the complete selected-week snapshot to the configured weekly LLM, splitting sequentially into requests under the configured byte ceiling when needed. Require JSON in the form `{"leads":[{"description":"...","source_event_ids":[1]}]}`. Every cited ID must belong to that batch's input. Exact duplicate descriptions across batches are consolidated and citations combined; no event content is discarded. The prompt asks the model to prioritize the most business-significant leads and return fewer concise leads rather than exceed its output-token limit.
3. For an empty week, still call the LLM and require `{"leads":null}`. Store the successful `no_events` attempt, but create no lead rows or vectors. A non-empty week with no leads uses `{"leads":[]}`.
4. Embed each lead's description with the hosted Voyage model. Dates, target IDs, and source-event IDs are stored as metadata, not embedded.
5. In one database transaction, store leads/vectors and mark the new successful run current. A failed run leaves the previous successful lead set current. A successful rerun supersedes the earlier current run for that target/week.

There is no weekly input compaction or cache. Batches include the full system prompt and use a conservative 48,000-byte default ceiling. If one event cannot fit by itself, the run fails without omitting or truncating it. Invalid JSON, invalid citations, provider errors, or embedding errors are recorded as explicit failures. Provider 4xx responses are non-retryable. Failed runs return HTTP 502 with the saved attempt details rather than a success-shaped 200. The process lock and APScheduler are process-local. A single retry is scheduled after retryable failures; startup recovery also processes the last closed week even when there were no daily events, so the empty-week contract is honored.

### Weekly API

All routes require authentication and enforce target ownership:

- `POST /targets/{target_id}/weekly/run?week_start=YYYY-MM-DD` — run a Monday-start week; defaults to the last closed week. The response includes raw `llm_response` and `error` fields; `error` is `null` on success, and `llm_response` is `null` when the model did not return text.
- `GET /targets/{target_id}/weekly` — list each week's current lead run.
- `GET /targets/{target_id}/weekly/{week_start}` — current run and leads.
- `GET /targets/{target_id}/weekly/{week_start}/runs?include_input=true` — attempt history; full audit payload is optional.

## Signal Detector

The Signal Detector is a separate stage scheduled Monday at 04:00 Asia/Kolkata. It starts only when the target's current weekly run for the selected week succeeded (`succeeded` or `no_events`) and its lead set is current. At 04:00 it checks the weekly handoff; if weekly processing is absent or still running, it checks again every 30 minutes, for three total checks ending at 05:00. If the first weekly attempt failed retryably, the handoff makes one weekly retry; if that does not succeed, signal detection is skipped. The signal stage itself has no automatic retry, but can be manually rerun.

History retrieval is scoped by target ID. The current Kundali rebuild endpoint updates the Kundali in place without clearing the target's collected events or weekly lead history, so a same-ID rebuild can still retrieve pre-rebuild leads. A full-history reset on that endpoint is not part of this Signal Detector implementation.

1. Load the ready target, its current Kundali, and its current lead run for the selected Monday. A missing successful lead run or Kundali prevents analysis.
2. Load all current-week leads. For each lead vector, retrieve the three nearest same-target leads from the 12 weeks before `week_start`; never retrieve the current week as history. PostgreSQL uses pgvector cosine distance; SQLite tests rank cosine similarity in Python. Historical rows are restricted to the active successful weekly-lead run for each prior week.
3. Deduplicate historical candidates returned for multiple current leads. Keep their IDs, Monday dates, similarity values, and the current-lead IDs that retrieved them. Similarity indicates candidate relevance, not proof.
4. Send the Kundali, all current leads, and retrieved historical candidates to the configured LLM. If there are no current leads, still send the baseline with empty current/history lists.
5. Measure the request size as a conservative UTF-8 byte count, including the system prompt. Keep it below the configured byte ceiling (default 49,000, below 50,000 bytes) as a conservative proxy for the agreed below-50,000-token cap. If needed, drop historical candidates from older weeks first; preserve the Kundali and current leads. If those alone exceed the budget, fail visibly.
6. Require signals with a title, description, allowed type(s), qualitative confidence and rationale, at least one current lead ID, and optional historical lead IDs. Validate all citations against the exact current leads and retrieved candidates. `continuation`, `acceleration`, `deceleration`, and `strategic_shift` require historical citations. Signals remain LLM judgments; confidence is not a calibrated probability.
7. Store each attempt and validated signals in `signal_detection_runs` and `detected_signals`. Failed attempts do not replace the previous current signal set. A successful rerun supersedes the earlier current run while retaining its history.

Owner-scoped signal endpoints:

- `POST /targets/{target_id}/weekly/signals/run?week_start=YYYY-MM-DD` — manually run detection; defaults to the last closed week and requires successful weekly leads.
- `GET /targets/{target_id}/weekly/{week_start}/signals` — current signal set and no-match reason.
- `GET /targets/{target_id}/weekly/{week_start}/signals/runs?include_input=true` — attempt history and optional evidence payload.

### Legacy replay and inspection

These scripts exercise the earlier weekly signal-analysis pipeline, not the active lead endpoint. They remain available for compatibility and evaluation of the legacy helpers.

- `python scripts/build_weekly_input.py --target N --week YYYY-MM-DD` prints an assembled input.
- `python scripts/replay_weekly.py --target N --weeks K` replays the most recent closed weeks and writes a Markdown report. The default fake LLM is not a quality evaluator; it checks pipeline/report mechanics without an LLM call. `--live` uses the legacy replay provider configuration. Replay applies a week-end cutoff to events, summaries, and Kundali snapshots and does not write weekly run/result rows. It may call the hosted Voyage API and persist missing vectors.
- `python scripts/embed_backfill.py --target N` resumes embedding backfill in batches.

## Data Model Summary

| Table | Role |
|---|---|
| `users`, `targets` | Accounts and owner-scoped target configuration |
| `kundali` | One current Day 0 profile per target |
| `events` | Unified source event store; includes collection date, source event time, baseline flag, tags, and date basis |
| `daily_source_checkpoints` | Per-source collection state and first-snapshot markers |
| `daily_source_summaries` | Per-target/date/source status, highlight, counts, and ATS/blog coverage breakdown |
| `event_embeddings` | Versioned 256-dimensional Voyage vector per event/model/preparation version |
| `weekly_lead_runs` | Every weekly lead extraction attempt, input/output, status, and model metadata |
| `weekly_leads` | Lead descriptions, citations, Monday week date, and 256-dimensional vector |
| `signal_detection_runs` | Signal extraction attempts, evidence input, model metadata, status, and retry information |
| `detected_signals` | Validated signal text/types/confidence and cited current/historical weekly lead IDs |
| `weekly_runs`, `weekly_results`, `weekly_signals` | Legacy signal-analysis tables; retained but no longer written by the active weekly detector |

Alembic manages schema changes. PostgreSQL is the active cloud database configured in the owner's ignored `.env`; SQLite migrations are also kept working and exercised on temporary copies. Apply `python -m alembic upgrade head` to the same database used by the app before deploying the 256-dimensional hosted Voyage embeddings. The transition migration deletes only incompatible derived vectors; events remain and are re-embedded on demand. Never run destructive migration or data-copy commands against the original local `ravn.db`.

## Tests and Operational Limits

Run the offline suite with `python -m unittest discover -s tests -v`; it uses SQLite and fakes and does not call external APIs or download a local embedding model. Optional live Supabase and hosted-model checks are explicitly gated. Use `python -m compileall app scripts` for a syntax pass.

The in-process scheduler is not a distributed scheduler, the weekly model may be unavailable or rate-limited on its free tier, historical source dates may be incomplete, semantic embeddings are not topic tags, and the LLM may propose weak correlations that citation verification cannot judge semantically. The planned narrator may improve explanation, but it is not implemented here.
