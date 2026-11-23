# RAVN

### Follow companies. See what's changing.

**RAVN is a company activity tracker for the tech ecosystem.** It monitors public engineering, hiring, and product activity, connects developments across time, and turns scattered signals into source-backed updates.

Think of it as a news feed built around **companies you care about**—not headlines scraped from the same outlets, but changes observed in their public footprint.

> **In development:** The data collection and historical signal-analysis backend is working. The public company directory, following, personalized feed, and AI-curated briefings are the next product layer.

## Why RAVN?

Important company updates don't arrive in one place. A new repository appears on GitHub. A team starts hiring for a particular stack. An engineering blog publishes a deep dive. A product page changes.

Individually, these are small updates. Together, they can tell a useful story about **what a company is doing now**.

RAVN brings them into one timeline, adds historical context, and keeps the underlying evidence accessible—without pretending to know a company's private plans.

## From public activity to company intelligence

```text
                 Add a tech company
                         │
                         ▼
              Discover its public sources
                         │
                         ▼
          Kundali · Build a company baseline
                         │
                         ▼
          Daily Radar · Capture new activity
                         │
                         ▼
          Weekly Leads · Surface developments
                         │
                         ▼
       Signal Detection · Connect past and present
                         │
                         ▼
    Intelligence Desk · Research, verify, publish  [planned]
                         │
                         ▼
       Company briefings + personalized feed      [planned]
```

### What RAVN tracks today

| Source | What RAVN observes |
| --- | --- |
| **GitHub** | Public repositories, language mix, commit history, and new commits |
| **Careers** | Public job postings from supported ATS platforms and company career pages |
| **Engineering blogs** | Technical articles and engineering announcements |
| **Web updates** | Changes on configured public pages |

RAVN keeps source records, not just AI-written summaries. Coverage varies by company and source availability.

### What makes the updates useful

- **A baseline, not a blank slate.** RAVN builds an initial company profile (*Kundali*) before tracking new activity.
- **A timeline, not disconnected headlines.** Daily Radar records new events and preserves their source context.
- **Patterns over time.** Weekly analysis extracts cited leads and compares them with related leads from the preceding **12 weeks** and the company baseline.
- **Traceable conclusions.** Signal outputs reference saved leads and evidence; they are observations and interpretations, **not claims about an unannounced roadmap**.

## Where RAVN is heading

**Follow companies, not duplicate trackers.** The planned public directory will let anyone add a tech company and let others follow the same company. Its monitoring and analysis will be shared; each user will get a feed of the companies they follow.

**A weekly briefing worth reading.** The planned **Intelligence Desk** will combine RAVN's detected signals with additional official-company research and relevant industry context. It will assemble stories, check evidence, and publish source-linked briefings. YouTube and other public channels are potential additions where reliable access is available.

The editorial rule is simple: **report what was observed, explain the context, and distinguish facts from interpretation.**

## Built with

**Python · FastAPI · SQLAlchemy · PostgreSQL/pgvector · Voyage embeddings · Hosted LLMs**

The backend currently provides authenticated APIs for target onboarding, company baselines, daily events, weekly leads, and historical signal detection. SQLite is also supported for local development and tests.

For internals, see [System Design](docs/SYSTEM_DESIGN.md) and the [Implementation Guide](docs/IMPLEMENTATION_GUIDE.md).

## Run locally

Requires Python 3.11 and API keys for the integrations you use.

```bash
# Clone the repository, then from its root:
python -m pip install -r requirements.txt
# Copy .env.example to .env and configure your credentials
python -m alembic upgrade head
uvicorn app.main:app --reload
```

Set `DATABASE_URL` and `JWT_SECRET_KEY` in `.env`. For weekly intelligence, configure `OPENROUTER_API_KEY` and `VOYAGE_API_KEY`; onboarding and Kundali synthesis need a supported LLM provider key. An optional `GITHUB_TOKEN` improves GitHub API access.

Open **[localhost:8000/docs](http://127.0.0.1:8000/docs)** to try the current API. The web experience is still under development.

---

**RAVN — Follow the companies. Understand the changes.**
