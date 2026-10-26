# RAVN — watch-it

Automated competitive intelligence for tech & AI companies. RAVN watches a target company's public footprint continuously, builds deep historical context, and correlates signals across public channels (GitHub, ATS/careers, engineering blogs, web & social) to surface what a competitor is likely building — before they announce it.

---

## Current Implementation Status

| Component | Status | Details |
| :--- | :---: | :--- |
| **Authentication** | Completed | Sign-up, login, password hashing via bcrypt, JWT issuance, and protected routes with `HTTPBearer` support. |
| **Target Onboarding** | Completed | Dynamic URL normalization (accepts `stripe.com` or `https://stripe.com`), automated source link discovery & classification via LLM. |
| **Kundali Builder (Day-0)** | Completed | Multi-channel ingestion across GitHub (repos, languages, 52-week cadence curve), ATS/careers (API + HTML scrapers), technical blogs (RSS + trafilatura HTML article extractor), and web/social snapshots. |
| **LLM Strategic Dossier** | Completed | Multi-provider router (Groq `openai/gpt-oss-120b` &rarr; Gemini `gemini-3.8-flash` &rarr; OpenRouter) synthesizing executive summary, strategic themes, cross-signal correlations, and predictive outlook. |
| **Database Persistence** | Completed | SQLite (`ravn.db` for local dev) via SQLAlchemy 2.0 & Alembic migrations. Idempotent upsert of Kundali profiles with status tracking (`building` &rarr; `ready`). |
| **REST API** | Completed | Clean FastAPI router split (`/targets/preview`, `/targets?sync=true`, `/targets/{id}/kundali/raw`, `/targets/{id}/kundali/synthesis`, `/targets/{id}/kundali`, `/targets/{id}/kundali/build`). |
| **Daily Collector & Detector** | Planned | Next phase in the roadmap: scheduled daily ingestion into `events` table and weekly spike + trend signal detection. |

---

## Quickstart

### 1. Environment Setup

Create a `.env` file in the project root:

```env
DATABASE_URL=sqlite:///./ravn.db
SECRET_KEY=your-secret-key-here
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

Apply Alembic migrations to set up the SQLite database:

```bash
alembic upgrade head
```

### 3. Start Development Server

Run FastAPI with Uvicorn:

```bash
uvicorn app.main:app --reload
```

Interactive Swagger API documentation will be available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

---

## Verification & Testing

RAVN includes automated test and verification runners:

- **GitHub Ingestion Unit Tests**:
  ```bash
  python -m tests.test
  ```
  Validates account name parsing, organization vs user classification, repository listing, commit cadence history, and language byte-count aggregation against real endpoints.

- **Live Database Kundali Inspector**:
  ```bash
  python -m tests.retest
  ```
  Queries the latest generated target Kundali in `ravn.db`, reporting all raw signal counts and synthesized strategic intelligence themes.

---

## Architecture Documentation

- [docs/IMPLEMENTATION_GUIDE.md](file:///d:/Projects/AI/RAVN/RAVN/docs/IMPLEMENTATION_GUIDE.md) — Technical stack, folder layout, data models, and component flow.
- [docs/PROGRESS.md](file:///d:/Projects/AI/RAVN/RAVN/docs/PROGRESS.md) — Chronological progress tracker of completed and tested modules.
- [docs/COMPONENTS.md](file:///d:/Projects/AI/RAVN/RAVN/docs/COMPONENTS.md) — Specification and data contracts for all 9 platform components.
- [docs/RAVN_PROJECT_SCOPE.md](file:///d:/Projects/AI/RAVN/RAVN/docs/RAVN_PROJECT_SCOPE.md) — Product philosophy, boundaries, and three-stage intelligence cadence.
