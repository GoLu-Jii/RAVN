## Repository Structure

This guide covers the implemented runtime and the planned product architecture. Weekly analysis, the dashboard, and PDF export are still planned components.

```
ravn/
├── app/
│   ├── main.py                 # FastAPI app entrypoint
│   ├── config.py               # empty placeholder; environment reads are currently module-local
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
│   │   ├── collectors.py       # date-specific collection and per-source daily summaries
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
| Hosting | Railway or Render are proposed options; persistence and scheduler deployment are not configured/validated |

### Notes per component

**Auth** — `bcrypt` for password hashing and `PyJWT` for JWT issuance and validation, with FastAPI's `HTTPBearer` dependency for token authorization in Swagger UI. Auth endpoints accept JSON payloads.

**Onboarding (source discovery)** — `httpx` for page fetching, `BeautifulSoup4` for parsing homepage links, with dynamic URL normalization and LLM classification into GitHub / ATS / blog / web-social source categories.

**Kundali Builder** — `httpx` for GitHub REST API calls (languages, commit cadence, active repos), `feedparser` for RSS/Atom feeds with `trafilatura` HTML article scraping fallback for non-RSS blogs, and ATS API probing with HTML career portal fallback for company-hosted job sites. Derives a multi-track strategic intelligence dossier via LLM synthesis.

**Daily Collector** — accepts a collection date, defaulting to today in Asia/Kolkata, and stores full event detail plus one source summary for GitHub, ATS, blog, and web/social. The scheduled run remains at 23:59 Asia/Kolkata; manual runs use the same date window. Event one-liners and highlights use deterministic formatting, not an LLM. Pull request and dependency-change events remain part of the broader project scope but are not collected by this implementation yet.

The shared `_parse_date` in `app/daily/dates.py` normalizes aware datetimes into UTC for storage and assigns calendar dates after conversion to Asia/Kolkata. Naive datetimes are treated as UTC; date-only values keep their date. It accepts ISO strings (including `Z`), RFC-style strings, epoch seconds/milliseconds, and feedparser `time.struct_time`; unparseable values return `None`.

ATS/blog items with source dates are stored only when their Asia/Kolkata date matches the collection date. On a current-day run, undated ATS/blog listings are baselined on the first successful snapshot, then newly appearing listings are recorded with the time RAVN first observed them. Their `date_basis` is `first_observed`, not the employer's publication date. On past-date runs, undated listings are counted but not stored or used to initialize the current snapshot baseline. Past-date web/social collection is skipped because those snapshots cannot be reconstructed. Current-day first web/social snapshots are baseline records and hidden from the event feed.

The daily source summary includes `undated_items`, the number of ATS/blog candidates without a usable source date encountered on that collection run. A live fetch on 2026-11-06 against the configured ready targets found four target records but only one unique ATS URL and one unique blog URL (all four records point to Stripe): all 6 returned ATS listings and all 5 returned blog entries had dates after the date-extraction changes. On 2026-11-05 the earlier implementation returned 0/5 and 0/15 dated items. These are point-in-time samples, not representative measurements across companies.

The Stripe URL `https://stripe.com/careers` is not recognized as one of the supported ATS API hosts, so `get_ats_postings()` uses the company-page HTML fallback. It reads `datePosted` from JSON-LD `JobPosting` blocks on unseen detail pages (20 detail lookups maximum per collection, with request timeouts). Failed requests or missing JSON-LD dates remain undated; current-day listings use the existing baseline/first-observed handling, while past-date runs skip them. Existing event URLs are checked before detail-page requests.

For provider-specific adapters, Lever's live public `leverdemo` response (10 postings) included `createdAt` as a Unix-millisecond field; the code maps it as the source date. The [Lever Postings API documentation](https://github.com/lever/postings-api#api-methods) documents the public JSON postings endpoint, while the live payload confirms the field exists. Greenhouse's [Job Board API docs](https://docs.greenhouse.io/job-board) document `updated_at` in list responses and `first_published` on individual job responses. The adapter uses `updated_at` only to pre-filter candidates for the requested date, then checks the individual job's `first_published`. A live Stripe Greenhouse response on 2026-11-06 confirmed both fields; a sampled listing had `first_published` 2026-09-09 and `updated_at` 2026-10-29, confirming that edits can make the update date misleading. Ashby's [public job posting API docs](https://developers.ashbyhq.com/docs/public-job-posting-api) specify `publishedAt`.

Blog RSS entries use `published`, then `published_parsed`, `updated`, and `updated_parsed`. The HTML fallback uses a listing card's `<time datetime>` value; when absent, it checks trafilatura metadata on up to three unseen article pages. The same shared date parser handles these source values. Stripe sample cards and article metadata were spot-checked for matching published dates. A live GitHub Blog fetch also returned RSS publication timestamps for its selected posts.

### Planned weekly processing (not implemented)

The planned reporting week is Monday through Sunday, using Asia/Kolkata `collection_date` values. The weekly job is planned for 03:00 Asia/Kolkata each Monday. Embedding-based retrieval with a vector store and the LangGraph agent stage remain planned; there is no vector database or LangGraph pipeline in the current implementation.

The source checkpoint records whether the initial undated ATS/blog listing snapshot has been baselined. Existing checkpoints are unmarked by migration, so the first successful current-day collection baselines listings it sees; a historical catch-up run does not set this marker. Later unseen current-day listings are recorded as first-observed events.

The collector adds fixed source/type labels (`github`, `commit`; `hiring`, `job_posting`; `engineering`, `blog`; `web_update`). These are not the product/topic taxonomy needed for cross-source spike and trend analysis. The weekly detector and semantic tagging are not implemented. Recommended design to settle before detector work: a separate deterministic tagging step that assigns a versioned taxonomy at ingestion, so detector logic can focus on counts and correlation and existing events can be backfilled. This is a design recommendation, not implemented behavior.

**Event Store** — SQLite (`ravn.db`) + SQLAlchemy, using `JSON` columns for tags and Kundali signals. PostgreSQL deployment is not implemented or validated yet.

**Collection recovery and hosting caveats** — before starting APScheduler, app startup searches backward from yesterday for up to three completed dates without any summary row per ready target, stopping at the target's creation date. Set `RAVN_DAILY_CATCHUP_DAYS` to change the default count. It runs the same collector with the historical date: dated GitHub, ATS, and blog events can be recovered, while web/social and undated first-observed items are skipped. Existing source data may no longer expose deleted or edited items, so catch-up cannot guarantee a complete historical reconstruction. The in-process APScheduler remains at 23:59 Asia/Kolkata with its in-memory job store; catch-up happens before it starts. SQLite is the current local database; Railway/Render persistent-volume and multi-instance scheduler behavior have not been configured or validated.

**Testing status** — existing integration scripts remain manual print-and-inspect checks. Offline `unittest` suites cover source date parsing, blog date fallbacks, ATS JSON-LD and Greenhouse date handling, daily collector boundaries/deduplication/baselines/past dates/API output, and startup catch-up. Run them with `python -m unittest tests.test_daily_dates tests.test_blog_dates tests.test_ats_dates tests.test_daily_collector tests.test_scheduler_catchup -v`.

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
| `tags` | JSON array of fixed source/type labels today; semantic product/topic tags are not implemented |
| `event_date` | Source event time when available; otherwise the observation time for first-observed events |
| `date_basis` | `source` or `first_observed`; nullable only for any historical event that cannot be safely classified |
| `ingested_at` | when RAVN captured it |
| `is_baseline` | Hide initial web/social snapshots and initial undated ATS/blog listings from the daily event feed |

### `daily_source_checkpoints`
| Field | Notes |
|---|---|
| `target_id`, `source_type` | Unique checkpoint per target and source |
| `last_success_at` | Timestamp of the last successful source collection |
| `last_content_hash` | Most recent web/social snapshot hash, used to detect page changes |
| `undated_items_baselined_at` | Marks the initial undated ATS/blog listing snapshot so existing open listings are not emitted as new events |

### `daily_source_summaries`
| Field | Notes |
|---|---|
| `target_id`, `collection_date`, `source_type` | Unique source summary for each target and date |
| `status` | Stored values: `success`, `failed`, `skipped`, and `baseline_only` for historical baseline records. If no row exists, the API returns `not_recorded` because collection status is unknown. |
| `event_count` | Current-day events stored for this source |
| `undated_items` | Number of ATS/blog candidates without a usable source date on that collection run; defaults to 0. Historical counts cannot be reconstructed, so existing rows receive 0 during migration. |
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
4. **Daily Collector** (scheduled at 11:59 PM Asia/Kolkata, or manually triggered) — records activity for the selected date and stores one summary per configured source for that date.
5. **Event Store** (SQLite) — holds users, targets, Kundali profiles, daily events, checkpoints, and daily summaries. PostgreSQL support and weekly tables are planned.
6. **Weekly Signal Detector** (planned) — consumes events after a semantic tagging step has been designed and implemented, then detects single-week spikes and multi-week trends. Current source/type labels are insufficient; tagging ownership and taxonomy remain open design decisions.
7. **Weekly Narrator** (planned) — verifies and writes the executive digest.
8. **PDF Exporter** (planned) — generates a downloadable PDF of a digest, on request.
9. **Dashboard/UI** (planned) — will display one-liners, drilldowns, Kundali profiles, and digests, scoped per user.

**Guardrails:** public data only, graceful degradation when a source is unavailable, no manual noise-tuning UI. Daily collection records events without LLM ranking; weekly prioritization is planned.
