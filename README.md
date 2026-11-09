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
| **Database Persistence** | Completed | SQLAlchemy and Alembic support SQLite for tests and the existing Supabase PostgreSQL database for the app. Stores users, targets, Kundali profiles, daily events, source checkpoints, and per-date source summaries; PostgreSQL has pgvector enabled for the weekly stage. |
| **REST API** | Completed | FastAPI endpoints for onboarding and Kundali, plus daily collection, dated source highlights, event feed/detail, and saved summary dates (`/targets/{id}/daily/collect`, `/targets/{id}/daily/summaries`, `/targets/{id}/daily/summaries/dates`, `/targets/{id}/daily/events`). |
| **Daily Collector** | Completed | Collects source-dated items for a requested Asia/Kolkata date, uses first-observed dates for new undated current-day ATS/blog listings, and checks recent missing or fully failed dates in background catch-up after startup; scheduled at 11:59 PM IST. |
| **Weekly Signal Detector, Narrator & PDF Export** | Planned | Weekly analysis, digest generation, and PDF export remain future phases; weekly tables and jobs are not implemented. |

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

RAVN includes offline automated `unittest` suites for the daily collection pipeline and manual verification scripts for live integrations:

- **Weekly embedding tests**:
  ```bash
  python -m unittest tests.test_embeddings -v
  ```
  Uses fake vectors by default, so it does not download a model. The local model is loaded only when an event needs embedding; its first use downloads `BAAI/bge-small-en-v1.5` (384 dimensions) to the machine's model cache. To backfill stored non-baseline events, run `python scripts/embed_backfill.py` or scope it with `--target <id>`. The process resumes by skipping embeddings whose prepared text has not changed. Set `RAVN_TEST_REAL_EMBEDDINGS=1` to opt into the semantic similarity check, which loads the model.

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
