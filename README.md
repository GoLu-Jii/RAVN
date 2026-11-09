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
| **LLM Strategic Dossier** | Completed | Kundali synthesis requests `openai/gpt-oss-120b`, routed to Groq first with Gemini and OpenRouter fallbacks; produces executive summary, strategic themes, cross-signal correlations, and predictive outlook. |
| **Database Persistence** | Completed | SQLAlchemy and Alembic support SQLite for tests and the existing Supabase PostgreSQL database for the app. Stores users, targets, Kundali profiles, daily events/summaries, versioned event embeddings, and weekly run/result/signal records; PostgreSQL uses pgvector for weekly retrieval. |
| **REST API** | Completed | FastAPI endpoints for onboarding and Kundali, plus daily collection, dated source highlights, event feed/detail, and saved summary dates (`/targets/{id}/daily/collect`, `/targets/{id}/daily/summaries`, `/targets/{id}/daily/summaries/dates`, `/targets/{id}/daily/events`). |
| **Daily Collector** | Completed | Collects source-dated items for a requested Asia/Kolkata date, uses first-observed dates for new undated current-day ATS/blog listings, and checks recent missing or fully failed dates in background catch-up after startup; scheduled at 11:59 PM IST. |
| **Weekly Signal Detector** | Completed | Scheduled and manual weekly analysis, local embeddings, pgvector retrieval, structured signal proposals, code-verified citations/scores/labels, storage, APIs, and replay harness. |
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
OPENROUTER_API_KEY=your-openrouter-key-optional
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
  Uses fake vectors by default, so it does not download a model. The local model is loaded only when an event needs embedding; its first use downloads `BAAI/bge-small-en-v1.5` (384 dimensions) to the machine's model cache. To backfill stored non-baseline events, run `python scripts/embed_backfill.py` or scope it with `--target <id>`. The process resumes by skipping embeddings whose prepared text has not changed. Set `RAVN_TEST_REAL_EMBEDDINGS=1` to opt into the semantic similarity check, which loads the model.

- **Weekly retrieval tests**:
  ```bash
  python -m unittest tests.test_weekly_retrieval -v
  ```
  Checks target and date-window isolation, exact similarity ordering, merged matches, age metadata, weekly counts, and GitHub grouping. The optional PostgreSQL smoke test also compares pgvector ordering with the Python cosine fallback.

- **Weekly signal verification/scoring tests**:
  ```bash
  python -m unittest tests.test_weekly_verification_scoring -v
  ```
  Covers citation rejection rules, source score examples, and code-derived spike/trend labels.

- **Weekly result persistence**:
  ```bash
  python -m unittest tests.test_weekly_repository -v
  ```
  Checks attempt history, atomic result replacement, failed-run safety, and owner-scoped lookups. Apply the schema with `python -m alembic upgrade head`.

- **Weekly analysis API** (requires the same bearer token as the other target routes):
  - `POST /targets/{target_id}/weekly/run?week_start=YYYY-MM-DD` runs a Monday-to-Sunday week; omit `week_start` for the last closed week.
  - `GET /targets/{target_id}/weekly` lists saved results.
  - `GET /targets/{target_id}/weekly/{week_start}` returns a result and its signals.
  - `GET /targets/{target_id}/weekly/{week_start}/runs` lists attempts; add `include_input=true` only when you need the saved full input and raw output.

- **Inspect a weekly LLM input**:
  ```bash
  python scripts/build_weekly_input.py --target 5 --week 2026-11-02
  ```
  The week must start on Monday. The script prints the JSON payload and its token estimate; if it cannot preserve all current-week events within the configured budget, it reports `input_over_budget` instead of dropping them.

- **Replay closed weeks for evaluation** (fake LLM by default; writes a Markdown report):
  ```bash
  python scripts/replay_weekly.py --target 5 --weeks 8
  ```
  Add `--live` only to use the configured Groq model. Replay rebuilds each week's input with a historical cutoff and does not save weekly runs or results. Missing embeddings are backfilled locally.

  Weekly analysis uses the pinned `RAVN_WEEKLY_WEEKLY_LLM_MODEL` (default `openai/gpt-oss-120b`) through the existing Groq client. The weekly call disables cross-provider fallback; configure the existing `GROQ_API_KEY` to make a live call. Offline tests use a fake client and do not consume provider quota.

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

- [docs/IMPLEMENTATION_GUIDE.md](docs/IMPLEMENTATION_GUIDE.md) — Technical stack, folder layout, data models, and component flow.
- [docs/PROGRESS.md](docs/PROGRESS.md) — Chronological progress tracker of completed and tested modules.
- [docs/COMPONENTS.md](docs/COMPONENTS.md) — Specification and data contracts for all 9 platform components.
- [docs/RAVN_PROJECT_SCOPE.md](docs/RAVN_PROJECT_SCOPE.md) — Product philosophy, boundaries, and three-stage intelligence cadence.
