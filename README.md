# RAVN — watch-it

Competitors rarely announce their next move first. Their clues are already public—in code, hiring, engineering posts, and product updates. RAVN connects those signals to reveal where they’re headed.

UNDER CONSTRUCTION

---

## Current Implementation Status

| Component | Status | Details |
| :--- | :---: | :--- |
| **Authentication** | Completed | Sign-up, login, password hashing via bcrypt, JWT issuance, and protected routes with `HTTPBearer` support. |
| **Target Onboarding** | Completed | Dynamic URL normalization (accepts `stripe.com` or `https://stripe.com`), automated source link discovery & classification via LLM. |
| **Kundali Builder (Day-0)** | Completed | Multi-channel ingestion across GitHub (repos, languages, 52-week cadence curve), ATS/careers (API + HTML scrapers), technical blogs (RSS + trafilatura HTML article extractor), and web/social snapshots. |
| **LLM Strategic Dossier** | Completed | Kundali synthesis requests `openai/gpt-oss-120b`, routed through the existing Groq/Gemini/OpenRouter fallback path; produces executive summary, strategic themes, cross-signal correlations, and predictive outlook. |
| **Database Persistence** | Completed | SQLAlchemy and Alembic support SQLite for tests and the existing Supabase PostgreSQL database for the app. Stores users, targets, Kundali profiles, daily events/summaries, event embeddings, and weekly lead/run vectors; PostgreSQL uses pgvector for vector storage. |
| **REST API** | Completed | FastAPI endpoints for onboarding and Kundali, plus daily collection, dated source highlights, event feed/detail, and saved summary dates (`/targets/{id}/daily/collect`, `/targets/{id}/daily/summaries`, `/targets/{id}/daily/summaries/dates`, `/targets/{id}/daily/events`). |
| **Daily Collector** | Completed | Collects source-dated items for a requested Asia/Kolkata date, uses first-observed dates for new undated current-day ATS/blog listings, and checks recent missing or fully failed dates in background catch-up after startup; scheduled at 11:59 PM IST. |
| **Weekly Lead Detector** | Completed | Extracts evidence-supported leads from one collected Monday–Sunday week, validates source-event citations, embeds lead text, and stores dated vectors with run history. |
| **Signal Detector** | Completed | Compares current weekly leads with the Kundali and top similar leads from the previous 12 weeks, validates lead citations, and stores confidence-qualified signals separately. Scheduled Monday 04:00 IST after the weekly lead stage. |
| **Weekly Narrator & PDF Export** | Planned | LangGraph narrative workflow, user-facing digest, and on-demand PDF export are future phases. |

---

## Quickstart

### 1. Environment Setup

Copy `.env.example` to `.env` and set the credentials you use:

```env
DATABASE_URL=sqlite:///./ravn.db
JWT_SECRET_KEY=your-secret-key-here
GITHUB_TOKEN=your-github-token-optional
GROQ_API_KEY=your-groq-key
GEMINI_API_KEY=your-gemini-key
OPENROUTER_API_KEY=your-openrouter-key
VOYAGE_API_KEY=your-atlas-model-api-key
# Optional; accepts your existing ENDPOINT variable too.
VOYAGE_EMBEDDING_PROVIDER_ENDPOINT=https://ai.mongodb.com/v1/embeddings
```

Install dependencies:

```bash
pip install -r requirements.txt
```

### 2. Database Migrations

By default, Alembic creates or updates the local SQLite database:

```bash
alembic upgrade head
```

To use the existing Supabase PostgreSQL database instead, set `DATABASE_URL` in `.env` to the connection string from Supabase's **Connect** panel. URL-encode special characters in the database password. The same setting selects the database for the app and Alembic; tests create their own SQLite databases. Migrations create the RAVN tables in the empty `public` schema and enable pgvector in `extensions`. Keep the actual connection string out of source control.

To transfer the current SQLite data, first make a separate copy of `ravn.db` and run the SQLite migrations on that copy. Then run `python scripts/migrate_sqlite_to_postgres.py --source-db <path-to-migrated-copy>`. The script refuses to copy into non-empty PostgreSQL tables, preserves row IDs, advances sequences, and prints table counts. It never edits the original SQLite file.

### 3. Start Development Server

Run FastAPI with Uvicorn:

```bash
uvicorn app.main:app --reload
```

Interactive Swagger API documentation will be available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

---

## Verification & Testing

RAVN includes offline automated `unittest` suites for daily collection and weekly analysis, plus opt-in checks for live integrations:

- **Weekly embedding tests**:
  ```bash
  python -m unittest tests.test_embeddings -v
  ```
  Uses mocked HTTP responses by default and never contacts MongoDB. The app uses the hosted `voyage-4-lite` API with 256-dimensional vectors; configure `VOYAGE_API_KEY`. To backfill stored non-baseline events, run `python scripts/embed_backfill.py` or scope it with `--target <id>`. The process resumes by skipping embeddings whose prepared text has not changed. Set `RAVN_TEST_REAL_EMBEDDINGS=1` to opt into a live semantic check that uses your Voyage key and quota.

- **Weekly Lead Detector tests**:
  ```bash
  python -m pytest tests/test_weekly_detector.py tests/test_weekly_routes.py tests/test_weekly_scheduler.py -q
  ```
  Covers collection-date week boundaries, baseline exclusion, empty weeks, lead citation validation, vector persistence, reruns, failure safety, ownership, and scheduling.

- **Signal Detector tests**:
  ```bash
  python -m pytest tests/test_signal_detector.py -q
  ```
  Covers same-target prior-12-week vector retrieval, top-three candidate limits, deduplication, input budget priority, citation rules, persistence, and reruns.

- **Weekly retrieval tests**:
  ```bash
  python -m unittest tests.test_weekly_retrieval -v
  ```
  Checks legacy retrieval helpers (not used by the active Weekly Lead Detector), including target/date-window isolation, similarity ordering, merged matches, and weekly counts.

- **Weekly signal verification/scoring tests**:
  ```bash
  python -m unittest tests.test_weekly_verification_scoring -v
  ```
  Covers legacy signal-analysis helper code; the active Weekly Lead Detector validates IDs against its selected-week input and does not calculate scores or signal labels.

- **Weekly result persistence**:
  ```bash
  python -m unittest tests.test_weekly_repository -v
  ```
  Checks legacy result-table helpers. The active lead pipeline is covered by the detector tests above; apply the additive schema with `python -m alembic upgrade head`.

- **Weekly lead API** (requires the same bearer token as the other target routes):
  - `POST /targets/{target_id}/weekly/run?week_start=YYYY-MM-DD` runs a Monday-to-Sunday week; omit `week_start` for the last closed week. The response includes the raw `llm_response` and `error` (`null` on success; `llm_response` is `null` if no model response was received).
  - `GET /targets/{target_id}/weekly` lists each week’s current lead run and leads.
  - `GET /targets/{target_id}/weekly/{week_start}` returns that week’s current run and leads.
  - `GET /targets/{target_id}/weekly/{week_start}/runs` lists attempts; add `include_input=true` only when you need the saved full input and raw output.

- **Signal API** (same owner-scoped authentication):
  - `POST /targets/{target_id}/weekly/signals/run?week_start=YYYY-MM-DD` manually runs signal detection after a successful weekly lead run.
  - `GET /targets/{target_id}/weekly/{week_start}/signals` returns the current signal set.
  - `GET /targets/{target_id}/weekly/{week_start}/signals/runs?include_input=true` lists signal attempts and optionally their evidence input/output.

- The weekly detector runs Mondays at 02:00 Asia/Kolkata, processes all non-baseline events collected during the prior Monday–Sunday week, and calls the configured model even when the week has no events. Empty weeks are saved with `leads: null` and no vectors. Lead text alone is embedded; week dates, target IDs, and source-event IDs remain structured metadata.
- Signal detection runs Mondays at 04:00 Asia/Kolkata. It waits at 30-minute intervals for up to three total checks when weekly leads are still running, and allows one retry after a first retryable weekly failure. Signal input preserves the Kundali and current leads and is capped below 50,000 estimated tokens, trimming older historical candidates first.
- Both active detector stages use the configured OpenRouter model with provider fallback disabled. Configure `OPENROUTER_API_KEY` for weekly lead and signal calls, `GROQ_API_KEY` and other configured keys for Kundali synthesis, and `VOYAGE_API_KEY` for embeddings. Offline tests use fakes and do not consume provider quota. The weekly stage allows one retry after retryable failures.

- **Full offline gate**:
  ```bash
  python -m unittest discover -s tests -v
  ```

- **Daily Collector tests**:
  ```bash
  python -m unittest tests.test_daily_dates tests.test_blog_dates tests.test_ats_dates tests.test_daily_collector tests.test_scheduler_catchup -v
  ```
  Checks date normalization and boundaries, repeat-run deduplication, baselines, past-date behavior, ATS and blog date extraction, API output, and startup catch-up.

- **GitHub Ingestion Verification Script**:
  ```bash
  python -m tests.test
  ```
  Exercises account name parsing, organization vs user classification, repository listing, commit cadence history, and language byte-count retrieval against live GitHub endpoints. It prints results for inspection.

- **Live Database Kundali Inspector**:
  ```bash
  python -m tests.retest
  ```
  Queries the latest generated target Kundali in `ravn.db`, reporting all raw signal counts and synthesized strategic intelligence themes.

---

## Architecture Documentation

- [docs/WEEKLY_DETECTOR_PLAN.md](docs/WEEKLY_DETECTOR_PLAN.md) — Implemented Weekly Lead Detector contract and future Signal Detector design.
- [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md) — End-to-end system architecture, runtime flows, database design, and Mermaid diagrams.
- [docs/IMPLEMENTATION_GUIDE.md](docs/IMPLEMENTATION_GUIDE.md) — Technical stack, folder layout, data models, and component flow.
- [docs/PROGRESS.md](docs/PROGRESS.md) — Chronological progress tracker of completed and tested modules.
- [docs/COMPONENTS.md](docs/COMPONENTS.md) — Specification and data contracts for all 9 platform components.
- [docs/RAVN_PROJECT_SCOPE.md](docs/RAVN_PROJECT_SCOPE.md) — Product philosophy, boundaries, and three-stage intelligence cadence.
