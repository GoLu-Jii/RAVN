## Repository Structure

This guide covers the implemented runtime and the planned product architecture. Weekly analysis, the dashboard, and PDF export are still planned components.

```
ravn/
├── app/
│   ├── main.py                 # FastAPI app entrypoint
│   ├── config.py               # planned settings module; currently empty
│   │
│   ├── auth/
│   │   ├── routes.py           # signup/login endpoints
│   │   ├── models.py           # User model
│   │   └── utils.py            # password hashing, JWT handling
│   │
│   ├── targets/
│   │   ├── routes.py           # preview, confirm, list/status targets, Kundali endpoints
│   │   ├── models.py           # Target model
│   │   └── onboarding.py       # source discovery logic (scrape + LLM classify)
│   │
│   ├── kundali/
│   │   ├── models.py           # Kundali model (table: kundali)
│   │   ├── builder.py          # GitHub fetchers (list_repos, get_languages, get_commit_activity, get_recent_commits)
│   │   └── synthesis.py        # orchestrates 4 data tracks, aggregates signals, DB upsert, LLM strategic intelligence dossier
│   │
│   ├── daily/
│   │   ├── collectors.py       # current-date collection and per-source daily summaries
│   │   └── fetchers/
│   │       ├── github.py       # daily GitHub commit fetcher
│   │       ├── ats.py          # ATS API fetcher (Greenhouse/Lever/Ashby/Workable) + HTML career portal fallback
│   │       ├── blogs.py        # RSS/Atom feed parser + trafilatura HTML article fallback
│   │       └── socials.py      # web landing page & social updates
│   │
│   ├── events/
│   │   ├── models.py           # Event model (unified table)
│   │   └── routes.py           # drilldown/detail endpoints
│   │
│   ├── weekly/                 # planned; placeholder modules are empty
│   │   ├── models.py           # planned WeeklyDigest, WeeklyCategoryCount models
│   │   ├── detector.py         # planned spike + trend detection logic
│   │   └── narrator/           # planned Analyst / Verifier / Writer / Critic
│   │       ├── analyst.py
│   │       ├── verifier.py
│   │       ├── writer.py
│   │       └── critic.py
│   │
│   ├── export/
│   │   └── pdf.py              # planned PDF generation from a digest
│   │
│   ├── scheduler/
│   │   └── jobs.py             # APScheduler setup; daily job only
│   │
│   ├── llm/
│   │   └── clients.py          # model-aware multi-provider LLM router with provider-specific fallback order
│   │
│   └── db/
│       ├── database.py         # SQLAlchemy engine/session setup
│       └── base.py             # shared SQLAlchemy declarative base
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
| Database | SQLite (`ravn.db`, current implementation); PostgreSQL is a future deployment target |
| ORM / migrations | SQLAlchemy + Alembic |
| Scheduler | APScheduler |
| Current Kundali LLM routing | Groq (`openai/gpt-oss-120b`) → Gemini → OpenRouter (fallbacks for that requested model) |
| Auth | bcrypt + PyJWT + FastAPI `HTTPBearer` |
| Scraping / fetching | httpx, BeautifulSoup4, feedparser, trafilatura |
| PDF export | WeasyPrint |
| Config | python-dotenv |
| Validation | Pydantic |
| Hosting | Railway or Render |

### Notes per component

**Auth** — `bcrypt` for password hashing and `PyJWT` for JWT issuance and validation, with FastAPI's `HTTPBearer` dependency for token authorization in Swagger UI. Auth endpoints accept JSON payloads.

**Onboarding (source discovery)** — `httpx` for page fetching, `BeautifulSoup4` for parsing homepage links, with dynamic URL normalization and LLM classification into GitHub / ATS / blog / web-social source categories.

**Kundali Builder** — `httpx` for GitHub REST API calls (languages, commit cadence, active repos), `feedparser` for RSS/Atom feeds with `trafilatura` HTML article scraping fallback for non-RSS blogs, and ATS API probing with HTML career portal fallback for company-hosted job sites. Derives a multi-track strategic intelligence dossier via LLM synthesis.

**Daily Collector** — collects events dated on the current Asia/Kolkata calendar date (00:00 through 23:59), then stores full event detail and one source highlight/status for each of GitHub, ATS, blog, and web/social. The scheduled run is at 23:59 Asia/Kolkata; manual runs use the same date window. Event one-liners and per-source highlights use deterministic formatting, not an LLM. Pull request and dependency-change events remain part of the broader project scope but are not collected by this implementation yet.

**Event Store** — SQLite (`ravn.db`) + SQLAlchemy, using `JSON` columns for tags and Kundali signals. PostgreSQL deployment is not implemented or validated yet.

**Weekly Signal Detector** — planned; the current `weekly/detector.py` is an empty placeholder.

**Weekly Narrator** — planned; the Analyst, Verifier, Writer, and Critic modules are empty placeholders.

**PDF Exporter** — planned; `app/export/pdf.py` is an empty placeholder.

---

## Data Model

Conceptual schema — fields and relationships, not raw SQL.

### `users`
| Field | Notes |
|---|---|
| `id` | PK |
| `email` | Unique |
| `password_hash` | bcrypt hashed |
| `created_at` | |

### `targets`
| Field | Notes |
|---|---|
| `id` | PK |
| `user_id` | FK → `users` |
| `name` | Target name |
| `website_url` | |
| `github_url`, `ats_url`, `blog_url`, `web_social_url` | nullable — any source may be missing |
| `status` | `building` / `ready` / `failed` |
| `created_at` | |

### `kundali`
| Field | Notes |
|---|---|
| `id` | PK |
| `target_id` | FK → `targets.id`, unique, one-to-one |
| `tech_stack` | JSON — languages detected and byte counts |
| `focus_areas` | JSON — LLM strategic intelligence dossier (executive summary, themes, correlations, forecasts) |
| `cadence_baseline` | JSON — 52-week commit activity distribution |
| `recent_commits` | JSON — repos with recent commits in 45-day window |
| `ats_postings` | JSON — active job requisitions from ATS/careers |
| `web_social_status` | JSON — landing page / social content snapshot |
| `blog_status` | JSON — cached blog articles / RSS feed entries |
| `created_at` | |

### `events` (unified table for all source types)
| Field | Notes |
|---|---|
| `id` | PK |
| `target_id` | FK → `targets` |
| `source_type` | `github` / `ats` / `blog` / `web_social` |
| `source_key` | Stable source identifier; unique with target and source type for deduplication |
| `title`, `url` | Event title and source link |
| `raw_content` | TEXT — full commit message / job post / blog text |
| `one_liner` | TEXT — dashboard summary |
| `collection_date` | Asia/Kolkata calendar date assigned to this daily collection |
| `tags` | JSON array |
| `event_date` | when the event happened |
| `ingested_at` | when RAVN captured it |
| `is_baseline` | Hide first-run baseline records, including initial web/social snapshots, from the daily event feed |

### `daily_source_checkpoints`
| Field | Notes |
|---|---|
| `target_id`, `source_type` | Unique checkpoint per target and source |
| `last_success_at` | Timestamp of the last successful source collection |
| `last_content_hash` | Most recent web/social snapshot hash, used to detect page changes |

### `daily_source_summaries`
| Field | Notes |
|---|---|
| `target_id`, `collection_date`, `source_type` | Unique source summary for each target and date |
| `status` | Stored values: `success`, `failed`, `skipped`, and `baseline_only` for historical baseline records. If no row exists, the API returns `not_recorded` because collection status is unknown. |
| `event_count` | Current-day events stored for this source |
| `highlight` | Concise stored summary; records “no events” when the source returns none |
| `error`, `collected_at` | Latest source error and collection time |

### Planned: `weekly_category_counts` (not in the current database)
| Field | Notes |
|---|---|
| `id` | PK |
| `target_id` | FK → `targets` |
| `week_start_date` | |
| `tag` | |
| `count` | number of events with this tag, for this target, in this week |

Planned purpose: let the Weekly Signal Detector compare recent weeks' tag activity without re-scanning full historical raw event content.

### Planned: `weekly_digests` (not in the current database)
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

The implemented pipeline uses bounded LLM calls for onboarding link classification and Day 0 Kundali synthesis. Daily collection is deterministic and does not call an LLM. The weekly detector and narrator remain planned.

**Components:**
1. **Auth Handler** — sign-up/login, session tokens, scopes all requests to the logged-in user.
2. **Onboarding Handler** — resolves a company website URL into confirmed source URLs (GitHub, ATS, blog, web/social), with user review.
3. **Kundali Builder** (background job) — builds the Day-0 historical baseline profile per target.
4. **Daily Collector** (scheduled at 11:59 PM Asia/Kolkata, or manually triggered) — records current-date activity and stores one highlight per configured source for that date.
5. **Event Store** (SQLite) — holds users, targets, Kundali profiles, daily events, checkpoints, and daily summaries. PostgreSQL support and weekly tables are planned.
6. **Weekly Signal Detector** (planned) — tags events and detects single-week spikes and multi-week trends.
7. **Weekly Narrator** (planned) — verifies and writes the executive digest.
8. **PDF Exporter** (planned) — generates a downloadable PDF of a digest, on request.
9. **Dashboard/UI** (planned) — will display one-liners, drilldowns, Kundali profiles, and digests, scoped per user.

**Guardrails:** public data only, graceful degradation when a source is unavailable, no manual noise-tuning UI. Daily collection records events without LLM ranking; weekly prioritization is planned.
