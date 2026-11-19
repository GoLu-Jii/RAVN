# RAVN

**See where competitors are heading, before they announce it.**

RAVN is a competitive intelligence engine for tech and AI companies. It continuously watches public signals across code, hiring, engineering content, and product updates—then connects them over time to surface evidence-backed clues about what a company may be building next.

Instead of repeatedly searching for updates, **add a company once and let RAVN build the context.**

> **Status:** Active development. The backend supports company onboarding, historical profiling, daily collection, weekly lead extraction, and signal detection. The web dashboard, narrated reports, and PDF export are not yet available.

## Why RAVN?

A competitor's next move rarely appears in a single announcement. It may show up as an unfamiliar repository, a cluster of engineering hires, or a series of subtle product changes.

These clues are scattered, easy to miss, and difficult to interpret without history. RAVN brings them together:

- **Continuous monitoring, not one-off searches.** Track a company's public footprint over time.
- **Context, not just updates.** Establish a baseline and compare new activity with what came before.
- **Connections, not isolated facts.** Look for patterns across sources and across weeks.
- **Evidence, not unsupported predictions.** Keep findings linked to the events and leads that support them.

## How it works

```text
Add a company
      ↓
Discover & confirm its public sources
      ↓
Day 0 · Kundali       Build an initial company profile and activity baseline
      ↓
Daily · Radar         Collect and preserve new public events
      ↓
Weekly · Leads        Extract noteworthy developments with source citations
      ↓
Weekly · Signals      Compare current leads with the baseline and prior weeks
```

### The intelligence RAVN builds

| Stage | What it gives you |
| --- | --- |
| **Kundali** | A starting picture of the company's public technology footprint, engineering activity, hiring, and strategic focus. |
| **Daily Radar** | A chronological record of new commits, job postings, engineering articles, and tracked web updates, with source details. |
| **Weekly Leads** | Significant observations extracted from that week's collected evidence, with validated references to source events. |
| **Signals** | Potential strategic patterns, informed by the company baseline and similar leads from up to 12 previous weeks, with qualitative confidence and supporting citations. |

**Example (illustrative, not a live RAVN result)**

> **Potential expansion into real-time data infrastructure**  
> New engineering roles and related repository activity may point to investment in streaming capabilities.  
> *Evidence: hiring + GitHub activity · Confidence: medium*

RAVN presents these as **evidence-backed hypotheses**, not certainty about a company's private roadmap.

## What RAVN monitors

| Source | Public activity tracked |
| --- | --- |
| **GitHub** | Repositories, language mix, commit history, and new commits |
| **Careers / ATS** | Job openings from supported boards and company career pages |
| **Engineering blogs** | Technical articles and engineering announcements |
| **Web / product updates** | Changes in configured public pages and update sources |

Coverage depends on what each company makes public. RAVN uses available sources rather than assuming every company has the same footprint.

## Built for traceability

RAVN stores underlying events, keeps a history of weekly analysis attempts, and validates the citations returned by its LLM stages. Weekly leads are embedded for historical retrieval; signal detection compares new leads with relevant earlier ones rather than treating each week in isolation.

The current backend is built with **Python, FastAPI, SQLAlchemy, PostgreSQL/pgvector, Voyage embeddings, and hosted LLMs**. SQLite is also supported for local development and tests.

For architecture, data models, operational details, and API contracts, see [System Design](docs/SYSTEM_DESIGN.md) and the [Implementation Guide](docs/IMPLEMENTATION_GUIDE.md).

## Try RAVN locally

**Requirements:** Python 3.11 and API credentials for the features you want to run.

1. Clone this repository, install dependencies, and copy `.env.example` to `.env`.

   ```bash
   python -m pip install -r requirements.txt
   ```

2. Configure `DATABASE_URL` and `JWT_SECRET_KEY`. Weekly analysis requires `OPENROUTER_API_KEY` and `VOYAGE_API_KEY`; onboarding/Kundali synthesis uses a configured LLM provider such as Groq. An optional `GITHUB_TOKEN` helps with GitHub API access. See `.env.example` for available settings.

3. Apply migrations and start the API.

   ```bash
   python -m alembic upgrade head
   uvicorn app.main:app --reload
   ```

4. Open **[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)** to sign up, authenticate, add a target company, inspect its Kundali and daily events, and run weekly lead/signal analysis.

The default local database is SQLite. For PostgreSQL with pgvector, configure `DATABASE_URL` before running migrations; details are in the [Implementation Guide](docs/IMPLEMENTATION_GUIDE.md).

---

**RAVN — watch it. Connect the signals.**
