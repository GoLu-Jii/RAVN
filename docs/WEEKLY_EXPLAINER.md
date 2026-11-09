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

## M4: Local event embeddings

**What it does:** RAVN stores a searchable 384-number vector for each non-baseline event using the free local `BAAI/bge-small-en-v1.5` model. New and changed events are embedded in batches after scheduled daily collection. A backfill command handles existing events.

**Why this design:** Similarity search needs a numeric representation, and the model runs on the developer's machine without a paid embedding API or another cloud service. PostgreSQL stores vectors through pgvector; SQLite uses JSON so local tests remain simple.

**How it works:** Source-specific deterministic text is prepared from each event and capped at 300 words. RAVN hashes that text and records the model and preparation version. The local model loads only when embeddings are needed, creates 384-dimensional vectors, and normalizes them. The service skips unchanged hashes, updates changed text, and commits in small batches so a failed run can resume. Failures are logged and do not break daily collection.

**Trade-offs:** The first embedding run downloads the model and can use CPU time. The source text is shortened, so some long descriptions are omitted. Embeddings are local and free; vector persistence and later search use the existing Supabase database.

**Likely questions:**

1. **Does this call a paid embedding API?** No. The pinned sentence-transformers model runs locally.
2. **What happens if embedding fails?** Daily collection remains successful; missing embeddings can be retried by the next scheduled run or backfill command.
3. **How do text changes refresh vectors?** A hash of the prepared text changes, so the existing event's embedding is updated.

## M5: Similarity retrieval

**What it does:** For each event this week, RAVN finds related events from the same target's preceding 12 weeks. It merges duplicate matches, counts matched events by week, and groups GitHub matches by repository and week.

**Why this design:** A company may discuss a topic weeks before it hires or changes its code. Similarity can connect that history across a flexible time gap. Age is reported separately so a later stage can consider recency without hiding semantic matches.

**How it works:** Current non-baseline events with embeddings become queries. RAVN uses one query for each current-week GitHub repository plus non-GitHub events, up to the configured query cap. Each query searches only earlier dates for the same target and matching model/preparation version. PostgreSQL orders candidates with pgvector cosine distance; SQLite uses exact Python cosine. Results are ranked by similarity alone, merged by event ID using the best similarity, and annotated with age in weeks and a two-week half-life weight. Per-week counts use distinct matched event IDs; GitHub matches are additionally grouped for compact display.

**Trade-offs:** Exact search scans the eligible target history and is simple to reason about; a vector index can be added if measured data volumes require it. Current events without embeddings cannot serve as search queries until the next embedding pass. The default 12-week window intentionally does not search older history.

**Likely questions:**

1. **Does recency change which matches are selected?** No. Similarity ranks results; age weight is separate metadata.
2. **Can one target retrieve another target's events?** No. Every search filters on the requested target ID.
3. **Are PostgreSQL and local results consistent?** Both use exact cosine similarity; the live Supabase smoke test compares their ordering on the same fixture.

## M6: Weekly input assembly

**What it does:** RAVN assembles the current week, source coverage, exact counts, retrieved history, and the Kundali snapshot into one JSON-safe input with a token estimate.

**Why this design:** Keeping assembly in ordinary code makes the data the model receives inspectable and repeatable. Current-week evidence and baseline context remain separate, and every current-week event stays represented even when the payload needs compaction.

**How it works:** The builder loads non-baseline events from the current week and prior 12 weeks, daily source summaries, and the target's Kundali. It computes source states and exact counts, labels first-observed events, formats the 52-week cadence and job/blog snapshots as baseline context, and attaches M5 retrieval results. It estimates tokens from serialized character length. If needed, it shortens text, groups current GitHub events by repository while retaining every event ID, and reduces retrieved examples. The applied compaction steps and omitted retrieved example IDs are recorded. If the input still exceeds its budget, the builder raises `input_over_budget`.

**Trade-offs:** The token estimate is approximate and intentionally conservative; the later LLM stage still records provider usage when available. Retrieved examples can be omitted under a tight budget, but their weekly match counts remain. Current events are never dropped.

**Likely questions:**

1. **Can compaction hide a current-week event?** No. GitHub events may be grouped, but each event's ID, date, date basis, title, and short text remain in the payload.
2. **How can I inspect the exact input?** Run `python scripts/build_weekly_input.py --target N --week YYYY-MM-DD`.
3. **What happens if the current week alone is too large?** The builder fails with `input_over_budget`; it does not silently remove current evidence.

## M7: Structured LLM analysis

**What it does:** The weekly LLM call turns the assembled evidence into a summary, data limitations, and candidate signals with source names and cited event IDs.

**Why this design:** A pinned model and strict response schema make runs comparable and catch malformed or unsupported output before later verification. Scores and signal types are deliberately absent from the LLM schema; code derives those later.

**How it works:** The versioned prompt labels the weekly input as untrusted evidence and tells the model to cite IDs, respect `first_observed`, and treat Kundali as undated context. The call requests only the configured Groq model and disables fallback providers. JSON/schema errors get up to two retries with the validation error. Provider failures are returned as retryable errors for the scheduler; a response truncated by the output limit fails immediately.

**Trade-offs:** The free provider can rate-limit or change availability, so a failure is visible and must be retried later. Strict validation may reject an otherwise readable response, which is safer than accepting unknown fields or invented scores.

**Likely questions:**

1. **Can a fallback model silently change the analysis?** No. Weekly requests explicitly disable cross-provider fallback.
2. **What does a retry handle?** Only invalid JSON or schema output, up to two retries; provider failures are left to scheduler retry.
3. **Can the model set confidence scores or spike/trend labels?** No. Those fields are not allowed in its schema and are computed by code later.

## M3: Week windows, coverage, and counts

**What it does:** The weekly foundation code identifies Monday-to-Sunday windows in Asia/Kolkata, calculates whether each source was checked, and counts non-baseline events for the current and preceding weeks.

**Why this design:** A source can have no events because it was quiet, because collection failed, or because the target was not configured for it. Activity and coverage are separate so the weekly analysis can describe those cases honestly.

**How it works:** Window helpers map dates and timezone-aware instants to the right Monday, generate the prior weeks, and find the last closed week. Coverage expects days starting on the target's creation date and ending yesterday. A `success` or `baseline_only` summary means that day was collected; missing, failed, and skipped summaries do not. ATS and blog coverage also checks the share of candidates missed by detail failures or caps, excluding already-known URLs. Counts include every non-baseline event in each week.

**Trade-offs:** Historical summaries created before the candidate counters were added contain zero counters, so their historical unverified share cannot be reconstructed. The collection-day records still show whether those older days succeeded.

**Likely questions:**

1. **Why are activity and coverage separate?** A company can have events during a week even if some source days failed to collect.
2. **Why not count days before target onboarding as missing?** RAVN could not have collected those days, so they are outside the expected coverage window.
3. **Why exclude known URLs from the unchecked share?** RAVN already has those listings; they are not a failed or skipped lookup.
