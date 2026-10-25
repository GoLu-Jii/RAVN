# RAVN — Build Progress Tracker

Last updated: 2026-10-19

Lists only what has been written and tested so far. No plans, no roadmap.

---

## app/db/base.py
- `Base` — SQLAlchemy declarative base, shared by all models.

## app/db/database.py
- `engine` — SQLAlchemy engine, connected via `DATABASE_URL` (SQLite, `sqlite:///./ravn.db`),
  loaded from `.env`. Uses `connect_args={"check_same_thread": False}` (SQLite-specific,
  needed since FastAPI can access a connection across threads).
- `SessionLocal` — session factory (`sessionmaker(bind=engine)`).
- `get_db()` — generator dependency function; yields a session, closes it in `finally`.

## app/auth/models.py
- `User` model (SQLAlchemy 2.0 `mapped_column` style)
  - Fields: `id` (PK), `email` (unique, not null), `password_hash` (not null),
    `created_at` (DateTime, timezone-aware, `server_default=func.now()`)

## app/auth/utils.py
- `hash_password(password: str) -> str` — bcrypt-based, salted, tested.
- `verify_password(password: str, hashed_password: str) -> bool` — tested against
  correct and incorrect passwords.
- `create_access_token(user_id: str) -> str` — builds JWT with `sub` + `exp` claims,
  signed with HS256, secret key loaded from `.env` via `os.getenv`.
- `verify_access_token(token: str) -> dict` — decodes JWT, does not catch exceptions
  internally (raises `ExpiredSignatureError` / `InvalidTokenError` to caller).
  Tested: valid token decodes successfully, expired token raises `ExpiredSignatureError`.

## Alembic
- Initialized (`alembic init alembic`), `alembic.ini` points at the SQLite `DATABASE_URL`.
- `env.py` wired to real metadata: imports `Base` and `User` (import needed so `User`
  actually registers itself on `Base.metadata` before autogenerate runs),
  `target_metadata = Base.metadata`.
- First migration generated and applied. Confirmed: `users` table physically exists in
  `ravn.db` with correct columns/constraints. `alembic_version` table present.

---

## app/auth/routes.py
- `router = APIRouter()`, mounted in `main.py` with `prefix="/auth"`.
- `CreateUser` (Pydantic schema, inline in routes.py) — `email`, `password`.
- `POST /auth/signup` — checks for existing email, hashes password via `hash_password()`,
  inserts new `User` row, returns `{id, email}`. Tested via Swagger UI — confirmed
  auto-increment `id` starts at 1, `password_hash` stored as real bcrypt hash (not plaintext).
- `POST /auth/login` — looks up user by email, verifies password via `verify_password()`
  against stored hash, issues JWT via `create_access_token()`. Returns
  `{access_token, token_type: "bearer"}`. Deliberately returns the same generic
  401 ("Invalid email or password") for both "no such user" and "wrong password",
  to avoid leaking which one was wrong. Tested: correct login, wrong password,
  nonexistent email — all confirmed working as expected.

## app/auth/routes.py (update)
- `oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")` — used only to extract
  the token from the `Authorization` header; `/login` itself still accepts JSON
  (`CreateUser` schema), not OAuth2 form data — deliberate choice, kept the API
  JSON-based rather than distorting it to satisfy Swagger's built-in Authorize UI.
- `GET /auth/me` — protected test route. Depends on `oauth2_scheme` to extract the
  token, calls `verify_access_token()`, catches `ExpiredSignatureError` and
  `InvalidTokenError` separately (distinct 401 messages), looks up the user by
  `sub` claim, returns `{id, email}` only (password_hash excluded from response).
  Tested end-to-end via PowerShell `Invoke-RestMethod`: login → capture token →
  call `/me` with `Authorization: Bearer <token>` → correctly returns identified user.

## app/auth/routes.py (update 2)
- `get_current_user` extracted from the `/me` route body into a standalone reusable
  dependency function, so it can be imported and used by `targets/routes.py` (and future
  protected routes) without duplicating the token-decode + user-lookup logic.
- Swapped `OAuth2PasswordBearer` for `HTTPBearer` as the auth scheme. Reason: `/auth/login`
  intentionally accepts JSON, not OAuth2 form data, which meant Swagger's built-in Authorize
  UI (built around `OAuth2PasswordBearer`'s username/password form) couldn't actually
  authenticate. `HTTPBearer` shows a plain token-paste field in Swagger instead, matching how
  tokens are actually issued and used in this API.

---

## app/main.py
- `FastAPI()` app instance, `auth_router` included with `prefix="/auth"`.
- Verified working end-to-end via `/docs` Swagger UI.

## app/main.py (update)
- `targets_router` included with `prefix="/targets"`, alongside the existing `auth_router`.

---

## app/targets/models.py
- `Target` model (SQLAlchemy 2.0 `Mapped[...]` style, matching `User`)
  - Fields: `id` (PK), `user_id` (FK → `users.id`), `name`, `website_url` (both not null),
    `github_url` / `ats_url` / `blog_url` / `web_social_url` (all nullable — any source may
    be missing), `status` (defaults to `"building"`), `created_at` (DateTime, timezone-aware,
    `server_default=func.now()`).

## Alembic (update)
- Second migration generated and applied (`add targets table`). `env.py` updated to also
  import `Target` (needed for autogenerate to detect it, same reason `User` had to be
  imported). First autogenerate attempt produced an empty migration because `Target` wasn't
  yet imported in `env.py`; regenerated after fixing the import and confirmed the migration
  correctly creates the `targets` table with the FK constraint to `users.id`.

---

## app/targets/onboarding.py
- `extract_links(url)` — fetches a single page via `httpx` (10s timeout), returns `[]` on
  request failure or status code >= 400 (no exception raised to caller). Parses HTML with
  BeautifulSoup, extracts every `<a>` tag's `href` + visible text, resolves relative URLs to
  absolute via `urljoin`. Tested against a real company homepage with mixed relative/absolute/
  anchor/mailto links — confirmed correct resolution.
- `discover_candidate_links(homepage_url)` — calls `extract_links` on the homepage plus a
  fixed fallback list (`/about`, `/careers`, `/blog`), merges all results (no dedup by
  design — cost asymmetry favors simplicity over cleanup on free tiers). Each page fetched
  independently; one failing page doesn't stop the others.
- `classify_links(links)` — sends the unfiltered link list to the shared LLM client
  (`app.llm.get_llm_client()`, JSON mode) with a system prompt instructing it to pick the best URL
  per category (`github_url` / `ats_url` / `blog_url` / `web_social_url`) or `null` if none match,
  with explicit "do not guess / never invent a URL" instructions.
- `parse_response(answer_text)` — wraps `json.loads` with a fallback to an all-null dict on
  `JSONDecodeError`, and automatically strips markdown code fences (e.g. ```` ```json ... ``` ````)
  before parsing so wrapped responses don't fail parsing.
- Tested end-to-end: negative case (a company with no GitHub/ATS/blog links anywhere on
  homepage + fallback pages) correctly returned all four fields as `null`, no hallucination.
  Positive case (Stripe) correctly identified real GitHub, careers, blog, and X/Twitter URLs
  from an unfiltered list of candidate links.

## app/targets/onboarding.py (update)
- Added `_BROWSER_HEADERS` with a standard Chrome `User-Agent` to `extract_links`, and enabled
  `follow_redirects=True` on `httpx.get`.
  - Issue fixed: Target sites (e.g. Stripe) issue 307 temporary redirects to country subpaths
    (`/in`) or enforce User-Agent checks, returning 0-byte responses when redirects were unfollowed.
  - Confirmed: With browser headers and redirect following enabled, Stripe extraction jumped from
    0 to 184 resolved links immediately.

---

## app/llm/clients.py & app/llm/__init__.py
- Single shared multi-provider LLM client (`LLMClient`, `get_llm_client()`, `chat_completion`):
  - **Primary Provider — OpenRouter**:
    - Connects to OpenRouter's OpenAI-compatible completions API using `OPENROUTER_API_KEY`.
    - Primary model: `liquid/lfm-2.5-2.6b:free` (0 cost on OpenRouter free tier).
    - Multi-model fallback array: passes up to 3 candidate models in OpenRouter's native `models`
      parameter (`["liquid/lfm-2.5-2.6b:free", "google/gemma-4-26b-a4b-it:free", "openrouter/free"]`).
      OpenRouter automatically handles provider-side 429s or downtime by routing to the next model
      in the list.
    - Legacy model mapping: if a model without `/` is passed (e.g. `llama-3.3-70b-versatile`), it
      safely maps to `DEFAULT_OPENROUTER_MODEL`.
  - **Fallback 1 — Google Gemini**:
    - Invoked automatically if OpenRouter fails (e.g. daily 50-request limit reached or network error).
    - Uses Gemini REST API (`gemini-3.8-flash`) via `x-goog-api-key` header with `GEMINI_API_KEY`.
    - Automatically sets `thinkingConfig: {"thinkingBudget": 0}` by default to prevent internal
      reasoning tokens from exhausting max output token budgets on structured JSON extraction tasks.
  - **Fallback 2 — Groq**:
    - Secondary backup calling Groq API via `GROQ_API_KEY` (`qwen/qwen3.8-27b` / `llama-3.3-70b-versatile`).
  - **Compatibility**:
    - Exposes `client.chat.completions.create(...)` matching Groq/OpenAI signatures.
    - Returns unified `LLMResponse` supporting `.choices[0].message.content`, `.content`, `.text`,
      `.provider`, and `.model`.
- Tested and verified:
  - OpenRouter primary: returns classified links in ~2 seconds with HTTP 200 at 0 cost.
  - Fallback failover: tested simulated failure of OpenRouter &rarr; successfully caught and fulfilled
    via Gemini fallback.

---

## app/daily/fetchers/
- Isolated fetchers for each external data source type:
  - `app/daily/fetchers/ats.py`:
    - `detect_ats_platform(ats_url)` — detects Greenhouse, Lever, Ashby, or Workable boards from URL.
    - `_extract_board_token(ats_url)` — extracts the company board slug.
    - `fetch_greenhouse_jobs`, `fetch_lever_jobs`, `fetch_ashbyhq_jobs`, `fetch_workable_jobs` —
      queries public ATS board APIs, normalizes listings into `[{title, location, url, posted_at}]`.
    - `get_ats_postings(ats_url)` — single entry point returning the list of active job postings.
  - `app/daily/fetchers/socials.py`:
    - `get_web_social_content(web_social_url)` — fetches public updates / social page via `httpx`
      (10s timeout) and extracts clean body text using `trafilatura`. Filters out short (<50 chars)
      or unextractable pages. Returns `{"url": ..., "content": ...}` or `None`.
  - `app/daily/fetchers/blogs.py`:
    - `discover_feed_url(blog_url)` — tests common feed paths (`/feed`, `/rss`, `/rss.xml`,
      `/atom.xml`, `/feed.xml`), verifying HTTP 200 and XML Content-Type.
    - `get_blog_posts(blog_url, max_posts=20)` — parses RSS/Atom entries via `feedparser`, fetches
      each post URL with `httpx`, extracts body content via `trafilatura`, returns normalized
      `[{title, link, published, content}]`.

---

## app/kundali/builder.py
- `extract_account_name(github_url)` / `get_account_type(name)` — resolves a GitHub URL to
  an account name and branches on whether it's an `Org` or `User` account type via
  `GET /users/{name}`, since orgs and users expose repos differently.
- `list_repos(github_url, limit)` — fetches the account's public repos, capped at `limit`,
  returns `[{"owner": ..., "repo": ..., "pushed_at": ...}, ...]`.
- `get_commit_activity(owner, repo, max_retries=5, wait_seconds=2)` — fetches 52 weeks of
  commit activity from `GET /repos/{owner}/{repo}/stats/commit_activity`. Retries only on
  `202` (GitHub still computing stats), fixed wait between retries; any other non-200 status
  fails immediately without retrying. Returns `None` if the repo never resolves out of `202`
  (e.g. zero-commit repos never get cached), or `[]`/data on `200`. Two distinct failure
  cases logged separately (network error vs. non-200 status vs. exhausted retries) so they're
  distinguishable.
- `get_recent_commits(owner, repo)` — last 45 days of commits via the `since` query param.
- `get_languages(owner, repo)` — byte-count-per-language dict from the repo's `languages`
  endpoint.
- All five functions unit-tested in `tests/test.py` (run via `python -m tests.test`),
  confirmed working against real GitHub orgs/users.

## app/kundali/models.py
- `Kundali` model — `id` (PK), `target_id` (FK → `targets.id`, unique, one-to-one), `tech_stack`
  (JSON), `focus_areas` (JSON, nullable), `cadence_baseline` (JSON), `recent_commits` (JSON),
  `ats_postings` (JSON, nullable), `web_social_status` (JSON, nullable), `blog_status` (JSON, nullable),
  `created_at` (DateTime, timezone-aware, server_default=func.now()).

## Alembic (update 2)
- Migration `b5922e36556c` — merged two divergent migration branches (target-side changes
  had branched independently of the kundali work).
- Migration `bed3128a8df6` — added the `kundali` table. `env.py` updated to import `Kundali`
  so autogenerate detects it. Confirmed: `kundali` table exists in `ravn.db`, schema matches
  the model 1:1, `alembic current` matches the latest migration file on disk (no drift).

## app/kundali/synthesis.py
- `_synthesize_focus_areas(tech_stack, cadence_baseline, recent_shifts, ats_postings, web_social, blog)` —
  LLM synthesis pass that derives `focus_areas` from the aggregated raw signals:
  - Trims payload to signal-bearing data only: top 8 langs by byte-count, repos sorted by
    recent commit volume, up to 30 ATS job titles, 15 blog titles, 400-char social snippet.
  - Calls `chat_completion()` with `model="openai/gpt-oss-120b"` and smart provider
    fallback across Groq, Gemini (`gemini-3.8-flash`), and OpenRouter, with
    `response_format={"type": "json_object"}`, `temperature=0.4`, `max_tokens=1024`.
  - System prompt instructs the LLM to identify connections between signals (GitHub repos/commits,
    job board postings, blog, social) to identify 3 to 5 concrete strategic investment themes
    with reasoning: `{"focus_areas": [...], "rationale": "..."}`.
  - Graceful degradation: any LLM failure is caught and logged; returns `None` so the
    DB write proceeds without crashing the whole build.
- `build_kundali(target_id, github_url, ats_url=None, web_social_url=None, blog_url=None)` —
  orchestrates the Day-0 profile build:
  1. `list_repos(github_url, limit=10)` for the target's repo subset.
  2. Per repo: `get_languages`, `get_commit_activity`, `get_recent_commits`.
  3. Aggregates `tech_stack` by summing byte-counts per language across all repos in the
     subset.
  4. Aggregates `cadence_baseline` by summing each week's commit total across all repos into
     a single 52-slot dict keyed by week index.
  5. Builds `recent_shifts` as a list of `{"repo": ..., "recent_commits": count}` entries for
     repos with any commits in the last 45 days.
  6. Fetches ATS postings via `get_ats_postings(ats_url)`: supports Greenhouse/Lever/Ashby/Workable APIs
     and automatically falls back to scraping job listing cards directly from company-hosted careers pages.
  7. Fetches web/social updates via `get_web_social_content(web_social_url)` if social URL present.
  8. Fetches recent blog posts via `get_blog_posts(blog_url)`: auto-discovers RSS/Atom feeds
     and falls back to extracting article cards and full text via `trafilatura` when no RSS feed exists.
   9. **LLM synthesis** — calls `_synthesize_focus_areas()` with the aggregated data to derive
      `focus_areas`. Uses `openai/gpt-oss-120b` with automated cross-provider fallback. Failure is
      gracefully degraded (writes `None` to `focus_areas` rather than crashing the whole build).
  10. Idempotent DB write: checks for existing `Kundali` row for `target_id`; updates all
      fields including `focus_areas` on existing records, or inserts a new record.
  11. Status management: flips `Target.status` from `"building"` &rarr; `"ready"` upon completion,
      or `"failed"` with rollback if an unhandled error occurs.
- Tested live on Target 4 (`stripe`): successfully generated full Kundali across all 4 channels:
  `tech_stack` (14 langs), `cadence_baseline` (52 weeks), `recent_commits` (10 repos), `ats_postings`
  (active roles), `blog_status` (recent articles), `web_social_status`, and multi-source
  synthesized `focus_areas`.

---

## app/targets/routes.py
- `POST /targets/preview` — takes `{website_url}`, runs `discover_candidate_links` →
  `classify_links`, returns the four-field result. No DB write. Protected via
  `Depends(get_current_user)`.
- `POST /targets` (`confirm`) — takes `{name, website_url, github_url, ats_url, blog_url,
  web_social_url}` with optional `?sync=true` query parameter.
  - When `sync=false` (default): creates `Target` row with `status="building"`, enqueues
    `background_tasks.add_task(build_kundali, ...)`, and returns `{id, name, status}` immediately.
  - When `sync=true`: creates target and runs `build_kundali` synchronously, returning the target
    together with the complete generated Kundali profile. Protected via `Depends(get_current_user)`.
- `GET /targets` — lists all targets belonging to `current_user`. Protected via
  `Depends(get_current_user)`.
- `GET /targets/{target_id}` — returns target details and current build status (`building`,
  `ready`, `failed`). Protected via `Depends(get_current_user)`.
- `GET /targets/{target_id}/kundali` — returns the generated Kundali profile, or
  `{"status": "building", "message": "Kundali profile is still building in background"}` if
  in-progress, or 404 if not found. Protected via `Depends(get_current_user)`.
- `POST /targets/{target_id}/kundali/build` — synchronously builds or refreshes the Kundali
  profile for `target_id` using its saved URLs directly from the database. **Requires zero request body.**
  Protected via `Depends(get_current_user)`.

---

## Verification & Test Scripts
- `tests/test.py` — GitHub parsing layer runner covering `extract_account_name`,
  `get_account_type`, `list_repos`, `get_commit_activity`, `get_recent_commits`, `get_languages`.
  Run via `python -m tests.test`.
- `tests/retest.py` — End-to-end database verification script: queries the database for target
  profiles and prints all six Kundali fields (`tech_stack`, `cadence_baseline`, `recent_commits`,
  `ats_postings`, `web_social_status`, `blog_status`). Run via `python -m tests.retest`.

---

## Scaffolding (not yet implemented)
Empty placeholder files exist for future components, created ahead of time to match the
repo structure in `IMPLEMENTATION_GUIDE.md` — no logic written yet:
- `app/config.py`
- `app/export/pdf.py`
- `app/scheduler/jobs.py`
- `app/daily/collectors.py`, `app/daily/fetchers/github.py`
- `app/events/models.py`, `app/events/routes.py`
- `app/weekly/detector.py`, `app/weekly/models.py`,
  `app/weekly/narrator/{analyst,critic,verifier,writer}.py`