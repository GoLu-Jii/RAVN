# RAVN — Build Progress Tracker

Last updated: 2026-09-19

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
  design — cost asymmetry favors simplicity over cleanup on Groq's free tier). Each page
  fetched independently; one failing page doesn't stop the others.
- `classify_links(links)` — sends the unfiltered link list to Groq (`llama-3.3-70b-versatile`,
  JSON mode) with a system prompt instructing it to pick the best URL per category
  (`github_url` / `ats_url` / `blog_url` / `web_social_url`) or `null` if none match, with
  explicit "do not guess / never invent a URL" instructions. `parse_response` wraps
  `json.loads` with a fallback to an all-null dict on `JSONDecodeError`.
- Tested end-to-end: negative case (a company with no GitHub/ATS/blog links anywhere on
  homepage + fallback pages) correctly returned all four fields as `null`, no hallucination.
  Positive case (Stripe) correctly identified real GitHub, careers, blog, and X/Twitter URLs
  from an unfiltered list of 558 candidate links.

## app/targets/routes.py
- `POST /targets/preview` — takes `{website_url}`, runs `discover_candidate_links` →
  `classify_links`, returns the four-field result. No DB write. Protected via
  `Depends(get_current_user)`.
- `POST /targets` — takes `{name, website_url, github_url, ats_url, blog_url,
  web_social_url}` (the user-confirmed/corrected sources from the preview step), creates a
  `Target` row scoped to `current_user.id`, returns `{id, name, status}`. Protected via
  `Depends(get_current_user)`.
- Both routes tested end-to-end via Swagger UI against Stripe: login → preview (correct
  classification) → confirm (row created, `status: "building"`).
- Fixed a naming mismatch between `preview`'s output keys and `ConfirmTarget`'s expected
  fields (`github`/`ats`/`blog`/`web_social` vs `github_url`/`ats_url`/`blog_url`/
  `web_social_url`) — `classify_links`'s prompt and fallback dict updated to use the `_url`
  suffixed names throughout, matching `ConfirmTarget` and the DB columns.

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
- `Kundali` model — `id` (PK), `target_id` (FK → `targets.id`, one-to-one), `tech_stack`
  (JSON), `focus_areas` (JSON, nullable), `cadence_baseline` (JSON), `recent_shifts` (JSON),
  `created_at`.

## Alembic (update 2)
- Migration `b5922e36556c` — merged two divergent migration branches (target-side changes
  had branched independently of the kundali work).
- Migration `bed3128a8df6` — added the `kundali` table. `env.py` updated to import `Kundali`
  so autogenerate detects it. Confirmed: `kundali` table exists in `ravn.db`, schema matches
  the model 1:1, `alembic current` matches the latest migration file on disk (no drift).

## app/kundali/synthesis.py
- `build_kundali(target_id, github_url)` — orchestrates the Day-0 profile build:
  1. `list_repos(github_url, limit=10)` for the target's repo subset.
  2. Per repo: `get_languages`, `get_commit_activity`, `get_recent_commits`.
  3. Aggregates `tech_stack` by summing byte-counts per language across all repos in the
     subset.
  4. Aggregates `cadence_baseline` by summing each week's commit total across all repos into
     a single 52-slot dict keyed by week index.
  5. Builds `recent_shifts` as a list of `{"repo": ..., "recent_commits": count}` entries for
     repos with any commits in the last 45 days.
  6. Writes one `Kundali` row via the existing `SessionLocal`/`get_db` pattern.
- `focus_areas` deliberately stubbed as `None` for this pass — deferred until the two-LLM-pass
  synthesis step from `IMPLEMENTATION_GUIDE.md` is built; raw-data aggregation is being
  proven end-to-end first.
- Partial-failure handling decided explicitly: if `get_commit_activity` returns `None`/`[]`
  for a given repo, that repo is skipped for cadence aggregation only (not the whole build) —
  matches the project's stated graceful-degradation principle. Other repos in the same subset
  still contribute their `tech_stack` and `recent_shifts` data regardless.
- Fixed three bugs found via static trace before first run (none of these were ever exercised
  successfully, so nothing above was "working then broken" — this was the first correct run):
  - `repo.get("name")` → `repo.get("repo")`, matching `list_repos`' actual key.
  - `recent_shifts` initialized as `{}` but used with `.append()` → initialized as `[]`.
  - `activity[51]` (single out-of-range week, iterated as dict keys) → `enumerate(activity)`
    (all 52 weeks), with `cadence_baseline.get(i, 0)` instead of unguarded `+=` on an unset key.
  - `db = next(get_db())` left the generator's `finally: db.close()` un-triggered → generator
    now held and explicitly `.close()`d after commit/refresh.
- Not yet done: no FastAPI route exposes `build_kundali` (not callable via API yet, only
  directly). `focus_areas` LLM pass not started.

## tests/test.py
- Rewritten into a runner script covering the GitHub parsing layer:
  `extract_account_name`, `get_account_type`, `list_repos`, `get_commit_activity`,
  `get_recent_commits`, `get_languages`. Run via `python -m tests.test` from project root.

---

## Scaffolding (not yet implemented)
Empty placeholder files exist for future components, created ahead of time to match the
repo structure in `IMPLEMENTATION_GUIDE.md` — no logic written yet:
- `app/config.py`
- `app/llm/clients.py`
- `app/export/pdf.py`
- `app/scheduler/jobs.py`
- `app/daily/collectors.py`, `app/daily/fetchers/{ats,blogs,github,socials}.py`
- `app/events/models.py`, `app/events/routes.py`
- `app/weekly/detector.py`, `app/weekly/models.py`,
  `app/weekly/narrator/{analyst,critic,verifier,writer}.py`