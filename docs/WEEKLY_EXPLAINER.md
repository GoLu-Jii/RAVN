# Weekly Signal Detector: plain-language notes

The weekly detector is being built in milestones. These notes explain the pieces already implemented; the detector itself is still under construction.

## M1: PostgreSQL and pgvector

**What it does:** RAVN can now use the existing Supabase PostgreSQL database, with pgvector enabled for the embeddings that will be added later. SQLite still works for local tests.

**Why this design:** PostgreSQL provides the durable tables the weekly stage needs, and pgvector lets those tables store and search numeric representations of events. Using the already available Supabase project avoids installing Docker or adding another service.

**How it works:** `DATABASE_URL` selects the active database for both the app and Alembic. SQLite gets its SQLite-only connection option; PostgreSQL does not. Alembic installs pgvector in Supabase's `extensions` schema. Existing SQLite data was copied from a separately upgraded snapshot, preserving IDs and resetting PostgreSQL sequences. The original `ravn.db` was left untouched.

**Trade-offs:** SQLite tests are fast and isolated but do not catch every PostgreSQL difference, so an opt-in PostgreSQL smoke test checks the real database path. The existing Supabase project is the chosen host; migrations must avoid touching its system schemas.

**Likely questions:**

1. **Why use pgvector?** It stores embeddings alongside the relational data and can search for similar events without a separate vector service.
2. **Why keep SQLite?** Unit tests remain fast, offline, and independent from the cloud database.
3. **How was the local data protected?** Migrations and copying used a separate SQLite snapshot; the original file was never upgraded or overwritten.

## M2: Daily candidate coverage counters

**What it does:** Each daily ATS and blog summary records how many listings were considered, how many were already known, how many detail lookups failed, and how many were skipped because of a lookup cap.

**Why this design:** A source with zero saved events can be quiet, or RAVN may have been unable to check some listings. These counters preserve that distinction for weekly coverage calculations.

**How it works:** Fetchers label unresolved listings as `known`, `failed`, or `cap_skipped`. The collector stores those counts in `daily_source_summaries` and returns them from collection and summaries endpoints. `not_checked_items` is the sum of failed and cap-skipped listings; known URLs are reported separately. Genuinely undated listings remain covered by the existing `undated_items` field.

**Trade-offs:** Old summary rows receive zeroes because the earlier runs did not save candidate counts. New runs can report coverage accurately; historical coverage cannot be reconstructed from the data that was saved.

**Likely questions:**

1. **Why separate known from unchecked?** A known URL was already stored, so it should not count as missing evidence.
2. **What does `not_checked_items` mean?** It counts detail fetch failures plus candidates left over by a fetch cap.
3. **Did the counters change which events are saved?** No. They describe collection coverage; the event-storage rules remain the same.
