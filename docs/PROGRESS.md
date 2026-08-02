# RAVN — Build Progress Tracker

Last updated: 2026-07-30

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
  Not yet exercised by a real route (will be used via `Depends(get_db)`).

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
- First migration generated and applied (`alembic revision --autogenerate` →
  `alembic upgrade head`). Confirmed: `users` table physically exists in `ravn.db`
  with correct columns/constraints. `alembic_version` table present (Alembic's own
  migration-state bookkeeping).

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

---

## app/main.py
- `FastAPI()` app instance, `auth_router` included with `prefix="/auth"`.
- Verified working end-to-end via `/docs` Swagger UI.