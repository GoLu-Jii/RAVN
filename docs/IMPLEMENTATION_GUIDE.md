## Repository Structure

```
ravn/
├── app/
│   ├── main.py                 # FastAPI app entrypoint
│   ├── config.py               # env vars, settings
│   │
│   ├── auth/
│   │   ├── routes.py           # signup/login endpoints
│   │   ├── models.py           # User model
│   │   └── utils.py            # password hashing, JWT handling
│   │
│   ├── targets/
│   │   ├── routes.py           # add target, list targets, delete target
│   │   ├── models.py           # Target model
│   │   └── onboarding.py       # source discovery logic (scrape + LLM classify)
│   │
│   ├── kundali/
│   │   ├── models.py           # KundaliProfile model
│   │   ├── builder.py          # orchestrates the 3-track GitHub + ATS + blog + web gather
│   │   └── synthesis.py        # the two LLM passes (baseline, recent-shift)
│   │
│   ├── daily/
│   │   ├── collectors.py       # scheduled daily job, per-source fetchers
│   │   └── fetchers/
│   │       ├── github.py
│   │       ├── ats.py
│   │       ├── blogs.py
│   │       └── socials.py
│   │
│   ├── events/
│   │   ├── models.py           # Event model (unified table)
│   │   └── routes.py           # drilldown/detail endpoints
│   │
│   ├── weekly/
│   │   ├── models.py           # WeeklyDigest, WeeklyCategoryCount models
│   │   ├── detector.py         # spike + trend detection logic
│   │   └── narrator/
│   │       ├── analyst.py
│   │       ├── verifier.py
│   │       ├── writer.py
│   │       └── critic.py
│   │
│   ├── export/
│   │   └── pdf.py              # PDF generation from a digest
│   │
│   ├── scheduler/
│   │   └── jobs.py             # APScheduler setup, registers daily/weekly jobs
│   │
│   ├── llm/
│   │   └── clients.py          # single shared wrapper around Groq API calls
│   │
│   └── db/
│       ├── database.py         # SQLAlchemy engine/session setup
│       └── base.py             # declarative base, shared imports for models
│
├── alembic/                    # DB migrations
├── tests/
├── requirements.txt
├── .env.example
└── README.md
```

Each top-level folder under `app/` maps to one component of the architecture, so component responsibilities and code locations stay aligned as the project grows.

---

# RAVN — watch-it

Automated competitive intelligence for tech & AI companies. RAVN watches a target company's public footprint continuously, builds historical context, and correlates signals across sources to surface what a competitor is likely building — before they announce it.

---

## Table of Contents
- [Tech Stack](#tech-stack)
- [Repository Structure](#repository-structure)
- [Data Model](#data-model)
- [Architecture Overview](#architecture-overview)

---

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python |
| Web framework | FastAPI |
| Database | PostgreSQL |
| ORM / migrations | SQLAlchemy + Alembic |
| Scheduler | APScheduler |
| LLM | Groq (free tier) |
| Auth | passlib (bcrypt) + python-jose / pyjwt + FastAPI `OAuth2PasswordBearer` |
| Scraping / fetching | httpx, BeautifulSoup4, feedparser, trafilatura |
| PDF export | WeasyPrint |
| Config | python-dotenv |
| Validation | Pydantic |
| Hosting | Railway or Render |

### Notes per component

**Auth** — `passlib`/`bcrypt` for password hashing, `python-jose`/`pyjwt` for JWT issuance and validation, built on FastAPI's built-in `OAuth2PasswordBearer` flow. No separate auth framework needed at this scale.

**Onboarding (source discovery)** — `httpx` for async page fetching, `BeautifulSoup4` for parsing homepage links, with a Groq LLM call classifying candidate links into GitHub / ATS / blog / web-social source categories.

**Kundali Builder** — `httpx` for GitHub REST API calls (or `PyGithub` as an alternative wrapper), `feedparser` for RSS/Atom feeds, `trafilatura` for clean main-text extraction from blog posts, and plain `httpx` calls to the Wayback Machine CDX API for historical backfill.

**Daily Collector** — reuses the same fetching tools as the Kundali Builder, run incrementally per source.

**Event Store** — PostgreSQL + SQLAlchemy, using `JSON`/`JSONB` columns for flexible fields (tags, tech stack, signals).

**Weekly Signal Detector** (`weekly/detector.py`) — pure Python logic; no additional library needed, since it's querying stored data and running comparison rules (both single-week spike checks and multi-week trend checks).

**Weekly Narrator** — a 4-role multi-agent loop (Analyst, Verifier, Writer, Critic), implemented as plain sequential function calls through a shared Groq client — deliberately no agent framework (e.g. LangChain/LangGraph), since the control flow is explicit and owned in code rather than delegated to a framework.

**PDF Exporter** — WeasyPrint, rendering an HTML/CSS digest template to PDF, generated on-demand when the user requests it. (Alternative considered: `reportlab`, for more manual layout control at the cost of more code — not needed here.)

---

## Data Model

Conceptual schema — fields and relationships, not raw SQL.

### `users`
| Field | Notes |
|---|---|
| `id` | PK |
| `email` | |
| `password_hash` | |
| `created_at` | |

### `targets`
| Field | Notes |
|---|---|
| `id` | PK |
| `user_id` | FK → `users` |
| `name` | |
| `website_url` | |
| `github_url`, `ats_url`, `blog_url`, `web_social_url` | nullable — any source may be missing |
| `status` | `building` / `ready` / `degraded` |
| `created_at` | |

### `kundali_profiles`
| Field | Notes |
|---|---|
| `id` | PK |
| `target_id` | FK → `targets`, one-to-one |
| `tech_stack` | JSON |
| `focus_areas` | JSON |
| `cadence_baseline` | JSON — e.g. weekly commit counts array |
| `recent_shifts` | JSON |
| `created_at` | |

### `events` (unified table for all source types)
| Field | Notes |
|---|---|
| `id` | PK |
| `target_id` | FK → `targets` |
| `source_type` | `github` / `ats` / `blog` / `web_social` |
| `tags` | JSON array, from the tag taxonomy |
| `raw_content` | TEXT — full commit message / job post / blog text |
| `one_liner` | TEXT — dashboard summary |
| `event_date` | when the event happened |
| `ingested_at` | when RAVN captured it |

### `weekly_category_counts` (lightweight, for trend comparison)
| Field | Notes |
|---|---|
| `id` | PK |
| `target_id` | FK → `targets` |
| `week_start_date` | |
| `tag` | |
| `count` | number of events with this tag, for this target, in this week |

Purpose: lets the Weekly Signal Detector compare recent weeks' tag activity without re-scanning full historical raw event content each run — this is what powers the multi-week trend check, alongside the current week's spike check.

### `weekly_digests`
| Field | Notes |
|---|---|
| `id` | PK |
| `target_id` | FK → `targets` |
| `week_start_date`, `week_end_date` | |
| `signals` | JSON — spike/trend signals with confidence levels |
| `narrative` | TEXT — the final digest text |
| `created_at` | |

---

## Architecture Overview

RAVN operates as a fixed, deterministic pipeline with LLM calls at specific, bounded steps — not an autonomous multi-agent system throughout. The one deliberate exception is the **Weekly Narrator**, a genuine generator–verifier multi-agent loop (Analyst → Verifier → Writer → Critic, with feedback edges), used specifically where verifying a claim before it reaches the user matters most.

**Components:**
1. **Auth Handler** — sign-up/login, session tokens, scopes all requests to the logged-in user.
2. **Onboarding Handler** — resolves a company website URL into confirmed source URLs (GitHub, ATS, blog, web/social), with user review.
3. **Kundali Builder** (background job) — builds the Day-0 historical baseline profile per target.
4. **Daily Collector** (scheduled) — logs new activity across all sources daily, in full detail, with one-liner summaries.
5. **Event Store** (PostgreSQL) — holds users, targets, Kundali profiles, daily events, and weekly category counts.
6. **Weekly Signal Detector** (scheduled) — tags events and detects both single-week spikes and multi-week trends.
7. **Weekly Narrator** (multi-agent) — verifies and writes the executive digest.
8. **PDF Exporter** — generates a downloadable PDF of a digest, on request.
9. **Dashboard/UI** — displays one-liners, drilldowns, Kundali profiles, and digests, scoped per user.

**Guardrails:** public data only, graceful degradation when a source is unavailable, no manual noise-tuning UI — all events are logged, prioritization happens inside the Weekly Signal Detector and Narrator, not through user curation.