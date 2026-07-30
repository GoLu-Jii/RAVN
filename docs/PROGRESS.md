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

## Working style notes (for Claude's reference across sessions)
- Gaurav writes all code himself. Claude explains concepts, points at what to write,
  reviews what's shared, asks debugging questions rather than handing over fixes.
- Full code from Claude only when explicitly requested as an exception.