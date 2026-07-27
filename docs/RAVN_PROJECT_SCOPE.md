# RAVN — Project Scope & Product Definition

**Tagline:** watch-it

---

## 1. Core Philosophy

Competitive intelligence today is reactive — you ask a question, a tool (or a person) answers based on what's already public and already known. RAVN flips this. It sits quietly in the background, continuously watching a target company's public footprint, connecting signals across different sources, and surfacing what a competitor is likely building — before they announce it.

RAVN is not a search tool. It is a standing, automated scout with memory.

---

## 2. Problem & Differentiation

| Traditional approach (manual research / ChatGPT-style tools) | RAVN |
|---|---|
| Reactive — you ask, it answers | Proactive — it watches, it tells you |
| Stateless — no memory of what it saw yesterday | Historical memory — tracks baselines over time |
| Single-source — evaluates one thing at a time | Cross-source correlation — connects dots across sources |
| Manual — requires you to search and prompt each time | Automated cadence — runs itself, reports on a schedule |

---

## 3. Target Scope

RAVN is scoped strictly to **tech and AI companies / startups**. Other domains (e.g. sports, entertainment, consumer brands) are explicitly out of scope, because they would require an entirely different set of data sources and detection logic. Staying narrow keeps the product coherent and the intelligence high-quality rather than generic.

---

## 4. Data Footprint

RAVN watches a standardized set of public sources for every target:

1. **GitHub** — public repositories, commit activity, pull requests, dependency/library changes
2. **ATS / career pages** — job postings from standard applicant tracking systems (Greenhouse, Lever, Ashby, Workable, etc.)
3. **Engineering blogs** — technical posts and dev announcements
4. **Social & web** — official product updates and launch pages

### Guardrails
- **Public data only.** RAVN never accesses anything beyond what is publicly visible. Platforms with no official public API and restrictive ToS (e.g. LinkedIn, Wellfound) are treated as best-effort web/social sources at most, never as dedicated integrations.
- **Graceful degradation.** If a source isn't available (e.g. a target's GitHub is private), RAVN doesn't break — it simply relies more heavily on whatever other sources are available.
- **No manual noise tuning.** There's no "mark as noise" step for the user to manage. Every event that's fetched is preserved; prioritization and signal-weighting happens inside the correlation engine itself, not through user babysitting.

---

## 5. Accounts & Onboarding

RAVN is a multi-user product — each user has their own account and their own set of tracked targets (single-owner per target for MVP; no shared/team access).

**Adding a target:**
1. The user provides the **main company website URL** for the target — nothing more.
2. RAVN attempts to auto-discover the attached sources: the company's GitHub org, ATS/careers board, and engineering blog.
3. RAVN presents what it found to the user for **review and confirmation**. If any discovered source is wrong or missing, the user corrects it before RAVN begins working.

This keeps onboarding effortless for the common case, while ensuring RAVN never quietly works from the wrong source.

**Deleting a target** performs a full purge — all events, Kundali profile, and digests tied to that target are permanently removed. No soft-delete/archive for MVP.

---

## 6. The Three-Stage Intelligence Cadence

```
  DAY 0: Onboarding             DAILY: The Radar             WEEKLY: The Digest
┌────────────────────┐       ┌───────────────────────┐     ┌────────────────────────┐
│   TARGET KUNDALI   │  ──>  │ Detailed Event Store  │ ──> │ Weekly Synthesis       │
│ (max available      │       │ (Full detail stored + │     │ (Correlates the week's │
│  historical context)│       │  one-liner UI feed)   │     │  events into strategy) │
└────────────────────┘       └───────────────────────┘     └────────────────────────┘
```

### Stage 1 — Day 0: The "Target Kundali" (initial deep profile)

When a target is added, RAVN immediately builds an initial profile — the **Target Kundali** (digital fingerprint) — by gathering the **maximum available public context** about the company, across however far back each source realistically allows:

- Core identity and primary focus areas
- What the company says about itself, what it has done, and what it says it plans to do
- Standard tech stack (languages, frameworks, infrastructure, tools) as seen in public repos
- Typical activity levels (e.g. rough commit cadence, rough hiring cadence) wherever there's enough history to establish one
- Any recent shifts — new tools, new hiring patterns, changes in tone or direction — that stand out against whatever historical baseline could be established

**Important honesty principle:** the depth of this baseline is not uniform across sources. GitHub history can usually be reconstructed quite far back. Job postings, blog history, and web/social updates often cannot be fully reconstructed retroactively — for those, RAVN gathers whatever historical trace is publicly findable, and the baseline **continues to mature** the longer RAVN watches the target going forward. RAVN doesn't overstate certainty it doesn't have — it just gathers the maximum honest context and gives the correlation engine (and the user) a starting point, not a false claim of complete history.

### Stage 2 — Daily: The Radar

Every day, RAVN logs new activity from the target's footprint.

- **Storage layer (full detail):** everything is stored in full — complete commit messages and dependency changes, full job posting text, full blog post text.
- **Presentation layer (UI):** the dashboard shows a clean, skimmable **one-liner per event**, so a daily check takes seconds, not minutes.
- **Drilldown:** clicking any one-liner expands to show the full underlying detail.
- Daily events are **not ranked or filtered** — everything is logged, chronologically, with no daily-level judgment call about what "matters." That judgment is reserved entirely for the weekly stage (see below), so RAVN only needs one place where real intelligence/prioritization happens.

### Stage 3 — Weekly: The Digest

Once a week, RAVN synthesizes the full week of detailed events (not just the one-liners) into an executive-level briefing.

**How correlation works (hybrid approach):**
1. **Signal detection (rule-based):** incoming events are tagged with simple categories (e.g. a repo tagged by its topic area, a job post tagged by its key skills). A rule scans the week's tagged data for two kinds of pattern: a **spike** — unusual overlap across multiple sources within the same week — and a **trend** — a category showing consistent or growing presence across the last 4–6 weeks, even if no single week alone looked dramatic. This step is simple, consistent, and fully explainable — it either finds a candidate pattern (of either kind) or it doesn't.
2. **Explanation (LLM-based, multi-agent):** only the candidate signals found in step 1 — along with their real underlying data — are handed to a small verification loop: a Signal Analyst selects what's worth reporting, a Verifier independently checks each candidate against the raw data for disconfirming evidence, a Writer drafts the narrative, and a Critic checks the draft for overstatement before it's finalized. The LLM is never asked to invent a correlation from raw data on its own; it only narrates and verifies a correlation that's already been detected by rules.

This hybrid design means RAVN's signals are always traceable back to real matching data (avoiding the risk of the LLM confidently describing a pattern that doesn't actually exist), while still getting a natural-language, executive-readable explanation.

**Confidence levels** fall naturally out of this design: a signal touching one source is low confidence, two overlapping sources is medium, three or more overlapping sources in the same window is high confidence.

**Output:** a concise executive report — what happened, what it likely means, and why it matters — exportable as a **PDF, generated on-demand when the user requests it** (not auto-generated every week).

---

## 7. Summary of Confirmed Product Decisions

- [x] Brand & messaging: RAVN — "watch-it"
- [x] Target scope: tech & AI companies/startups only
- [x] Data sources: GitHub, ATS/career feeds, engineering blogs, social & web
- [x] Accounts: multi-user, single-owner per target for MVP
- [x] Onboarding: user provides main website URL → RAVN auto-discovers sources → user confirms/corrects
- [x] Day 0 baseline: gather maximum available public history per source (not a uniform 9-month guarantee); baseline matures over time for sources without full retroactive access
- [x] Daily cadence: full detail stored, one-liner UI, no ranking/filtering at the daily level
- [x] Weekly cadence: hybrid rule-based signal detection (spike + trend) + multi-agent verified narrative, with confidence levels based on number of overlapping sources
- [x] Export: PDF weekly digest, on-demand only
- [x] Target deletion: full purge, no archive
- [x] Guardrails: public data only, graceful degradation when a source is unavailable, no manual noise-tuning UI

---

## 8. MVP Definition

The MVP proves the **full loop** — onboarding → baseline → daily logging → weekly correlated digest — for a small number of targets, without needing every source or every edge case perfectly handled.

**In scope for MVP:**
- User accounts (sign-up/login), single-owner per target
- Add a target via website URL, with source auto-discovery + manual user confirmation
- Day 0 profile built from whatever public history is available per source (GitHub prioritized for depth; others best-effort)
- Daily ingestion from all four source types, stored in full, shown as one-liners in a simple dashboard feed
- Drilldown from one-liner to full detail
- Weekly hybrid correlation (rule-based spike/trend tagging → multi-agent verified narrative) producing one executive digest per target
- PDF export of the weekly digest, on-demand
- Full purge on target deletion
- Graceful degradation when a source is missing (e.g. private GitHub) — pipeline continues on remaining sources

**Explicitly out of scope for MVP:**
- Any domain outside tech/AI targets
- Any historical backfill guarantees beyond "best available" — no promise of complete 9-month coverage for non-GitHub sources
- Any manual noise-tuning / feedback UI ("mark as noise," "hide this," etc.)
- Team collaboration, shared targets, permissions beyond single-owner
- Notification channels beyond the dashboard (e.g. email/Slack alerts) — can be a fast-follow, not MVP
- Any source beyond the four defined (no Twitter/X deep analytics, no patent filings, no funding-news scraping, etc.)

The MVP's success criterion: for a handful of real tech companies, RAVN should be able to onboard them, run daily for a week, and produce one weekly digest that a person would genuinely find useful and non-obvious — without any manual noise correction needed from the user.