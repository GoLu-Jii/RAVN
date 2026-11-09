# RAVN — Implementation Guide

This guide describes the current backend implementation. The frontend/dashboard, LangGraph narrator, and PDF export remain planned. The backend weekly signal detector is implemented.

## Repository Structure

```text
app/
  auth/          authentication routes, user model, password/JWT helpers
  targets/       onboarding, source discovery, target routes/model
  kundali/       Day 0 collection, profile model, LLM dossier synthesis
  daily/         daily collector and GitHub/ATS/blog/web-social fetchers
  events/        event/source-summary models and daily APIs
  embeddings/    local embedding model, text preparation, persistence service
  weekly/        config, windows, coverage, counts, retrieval, input, LLM,
                 verification, scoring, persistence, replay, routes, detector
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
| Embeddings | Local `BAAI/bge-small-en-v1.5`, 384 dimensions, sentence-transformers; no embedding API |
| Weekly LLM | Pinned `openai/gpt-oss-120b` through the existing Groq client, provider fallback disabled for weekly calls |
| Other LLM calls | Kundali synthesis and onboarding use the existing multi-provider routing/fallback behavior |
| Scheduling | In-process APScheduler with Asia/Kolkata timezone |
| Collection | httpx, BeautifulSoup, feedparser, trafilatura; source-specific public APIs where available |
| Tests | Python `unittest`; offline tests use fakes, optional live database/model checks are opt-in |

The project owner uses an existing free-tier Supabase project because Docker is unavailable. No second cloud service is required. `DATABASE_URL` selects the same database for the app and Alembic; never commit the URL. SQLite remains usable for local development and all unit tests. The scheduler uses an in-memory job store and a process-local lock, so only one application process should own scheduled jobs unless deployment-level coordination is added.

## Configuration

Generic settings include `DATABASE_URL`, `JWT_SECRET_KEY`, optional `GITHUB_TOKEN`, and provider keys such as `GROQ_API_KEY`. Weekly settings are read from environment variables named `RAVN_WEEKLY_<FIELD>`; defaults are defined in `app/weekly/config.py`.

| Variable | Default | Purpose |
|---|---:|---|
| `RAVN_WEEKLY_WEEK_START` | `monday` | Fixed Monday week boundary |
| `RAVN_WEEKLY_SCHEDULE_HOUR` / `MINUTE` | `3` / `0` | Monday run time in `Asia/Kolkata` |
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
| `RAVN_WEEKLY_WEEKLY_LLM_MODEL` | `openai/gpt-oss-120b` | Pinned weekly model (field name: `weekly_llm_model`) |
| `RAVN_WEEKLY_EMBED_MODEL_ID` | `BAAI/bge-small-en-v1.5` | Local embedding model identifier |
| `RAVN_WEEKLY_PREP_VERSION` | `v1` | Version of event text preparation |
| `RAVN_WEEKLY_RETRY_ATTEMPTS` | `6` | Scheduled retries after retryable failure |

The environment variable prefix is `RAVN_WEEKLY_` followed by the configuration field name uppercased (for example `RAVN_WEEKLY_WEEKLY_LLM_MODEL`). The Groq key is the existing `GROQ_API_KEY`. The replay command uses a fake no-signal client by default; `--live` explicitly enables the provider call.

## Daily Collection

`POST /targets/{id}/daily/collect` collects all configured sources for the current Asia/Kolkata day. The scheduler runs at 23:59. Dated GitHub commits, job listings, and blog posts are assigned by their source date after timezone normalization. On current-day runs, the first ATS/blog listing snapshot is baseline; later genuinely undated new listings can be stored with `date_basis=first_observed`. Known URLs, failed detail requests, and cap overflow are not stored as undated items. Past-date collection skips web/social snapshots and first-observed items. Daily collection does not call an LLM.

Daily summaries store one row per target/date/source and include source status, event count, highlight, undated count, and candidate-coverage counters. The event feed excludes baseline rows and raw content; the event detail endpoint includes raw content. The four source highlights are available by collection date.

After the scheduler starts, startup catch-up runs in a background thread for recent dates missing summaries or with all four sources failed, bounded by `RAVN_DAILY_CATCHUP_DAYS` (default 3). It can recover dated sources only; web/social snapshots and first-observed events cannot be reconstructed for past dates.

GitHub daily collection currently stores commits. Pull requests and dependency-change events remain in the broader product scope and are not implemented.

## Weekly Analysis Pipeline

The week is Monday through Sunday in Asia/Kolkata. The scheduled job runs Monday at 03:00 for the last closed week. Manual API runs can select any Monday. Each target/week has one latest successful result and a separate row for every attempt.

1. Embed missing or changed non-baseline events locally. The daily collection path also attempts embeddings after collection; failures do not fail collection.
2. Compile all non-baseline current-week events, source activity/coverage, exact per-source counts for this week and the prior 12 weeks, and a separate Kundali baseline context.
3. Retrieve same-target, non-baseline events from up to 12 earlier weeks. Search ranks by cosine similarity only; age and a two-week half-life weight are separate metadata. PostgreSQL uses pgvector; SQLite uses exact Python cosine.
4. Compact the serialized input within the configured estimate, preserving all current-week events. If it still exceeds budget, fail explicitly.
5. Ask the pinned Groq model for a summary, limitations, and signal proposals with source claims and event IDs. Weekly calls disable provider fallback. Invalid JSON/schema responses receive bounded correction retries; truncation and provider errors fail visibly.
6. Verify citations against the event table: target, window, baseline flag, claimed source, duplicate IDs, and at least one current-week citation. Record rejected proposals and source-claim mismatches.
7. Compute source scores and signal labels in code. A supported configured source scores 1; a complete source with no cited support scores 0; a partial/unavailable source scores 0.5; an unconfigured source scores 0 and is excluded from configured-source count. The total is a 0–4 evidence score, not a probability. A spike has current-week cited support from two or more sources; a trend has citations in three or more distinct weeks; otherwise a signal is isolated.
8. Persist the attempt, full input/output audit data, result summary, exact counts, source states, and verified signals. A failed attempt never replaces the previous successful result. Zero-event and no-verified-signal weeks get deterministic nonblank summaries.

The weekly process lock and APScheduler are process-local. Retry delays are 10, 30, 60, 120, 240, and 240 minutes. Startup recovery waits for daily catch-up and retries the last closed week only when collection exists and there is no success or terminal nonretryable failure.

### Weekly API

All routes require authentication and enforce target ownership:

- `POST /targets/{target_id}/weekly/run?week_start=YYYY-MM-DD` — run a Monday-start week; defaults to the last closed week.
- `GET /targets/{target_id}/weekly` — list saved weekly results.
- `GET /targets/{target_id}/weekly/{week_start}` — result and verified signals.
- `GET /targets/{target_id}/weekly/{week_start}/runs?include_input=true` — attempt history; full audit payload is optional.

### Replay and inspection

- `python scripts/build_weekly_input.py --target N --week YYYY-MM-DD` prints an assembled input.
- `python scripts/replay_weekly.py --target N --weeks K` replays the most recent closed weeks and writes a Markdown report. The default fake LLM is not a quality evaluator; it checks pipeline/report mechanics without a provider call. `--live` uses the configured Groq model. Replay applies a week-end cutoff to events, summaries, and Kundali snapshots and does not write weekly run/result rows. It may persist missing local embeddings.
- `python scripts/embed_backfill.py --target N` resumes embedding backfill in batches.

## Data Model Summary

| Table | Role |
|---|---|
| `users`, `targets` | Accounts and owner-scoped target configuration |
| `kundali` | One current Day 0 profile per target |
| `events` | Unified source event store; includes collection date, source event time, baseline flag, tags, and date basis |
| `daily_source_checkpoints` | Per-source collection state and first-snapshot markers |
| `daily_source_summaries` | Per-target/date/source status, highlight, counts, and ATS/blog coverage breakdown |
| `event_embeddings` | Versioned 384-dimensional vector per event/model/preparation version |
| `weekly_runs` | Every attempt, status/retry metadata, models, tokens, input/output, and rejection audit |
| `weekly_results` | Latest successful result and exact counts per target/week |
| `weekly_signals` | Verified signal descriptions, citations, scores, source states, labels, and claim audit |

Alembic manages schema changes. PostgreSQL is the active cloud database configured in the owner's ignored `.env`; SQLite migrations are also kept working and exercised on temporary copies. Never run destructive migration or data-copy commands against the original local `ravn.db`.

## Tests and Operational Limits

Run the offline suite with `python -m unittest discover -s tests -v`; it uses SQLite and fakes and does not call external APIs or download the embedding model. Optional live Supabase and real-model checks are explicitly gated. Use `python -m compileall app scripts` for a syntax pass.

The in-process scheduler is not a distributed scheduler, the weekly model may be unavailable or rate-limited on its free tier, historical source dates may be incomplete, semantic embeddings are not topic tags, and the LLM may propose weak correlations that citation verification cannot judge semantically. The planned narrator may improve explanation, but it is not implemented here.