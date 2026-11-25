# RAVN Intelligence Desk — Scope and Architecture

> **Status (November 2026):** Company Scout is connected to a LangChain agent and is being simplified to three read-only tools: weekly YouTube videos, YouTube transcripts, and webpage reading. Intelligence Desk editorial roles and the complete multi-agent workflow remain future work.
>
> **Budget:** No paid source access or paid research infrastructure. Free sources can still have quota, availability, and deployment limits.

## 1. Product direction

RAVN follows public company activity and produces source-backed weekly updates about engineering, hiring, products, and official announcements. Company Scout investigates the target company; it does not replace the existing Weekly Lead or Signal Detector pipelines.

The editorial promise is to show what a company publicly did, what changed, and what evidence supports the account. Do not present speculation as fact.

## 2. Existing intelligence foundation

```text
Target setup and supplied source links
                 ↓
Kundali — initial company profile and baseline
                 ↓
Daily Radar — collected public events
                 ↓
Weekly Leads — current-week lead summaries and event IDs
                 ↓
Signal Detector — current leads + Kundali + historical weekly-lead matches
                 ↓
Detected signals
```

Historical matching belongs to the Signal Detector. Company Scout receives only Weekly Leads for its reporting week; it does not perform historical retrieval or repeat signal detection.

## 3. Company Scout v1

Company Scout researches the target’s supplied website and YouTube channel for the reporting week. It receives the target identity, website URL, week window, and that week’s Weekly Leads. It may also find company updates unrelated to those leads.

The user manually supplies a distinct `youtube_url` on the target. The existing target-link preview remains optional for its current fields and does not suggest YouTube URLs. Existing `web_social_url`, daily collection, and Kundali behavior remain unchanged.

The agent gets three tools:

1. `get_weekly_youtube_videos(week_start, week_end)` — public video metadata from the target’s channel for that week.
2. `get_youtube_transcript(video_id)` — a best-effort transcript for a video returned by the first tool.
3. `read_webpage(url)` — static page text and links; the agent chooses whether to read a returned link by calling the same tool again.

The website reader uses HTTPX and BeautifulSoup. It does not run a sitemap crawl, classify page relevance, or render JavaScript. The agent decides what is relevant. The reader enforces basic public-URL, redirect, timeout, and response-size limits.

Instagram and X are excluded from Company Scout because free access is unreliable or requires deployment credentials. This does not remove or change existing RAVN collection fields or behavior.

### YouTube limits

Video discovery uses the YouTube Data API and `YOUTUBE_API_KEY`. The transcript package is free but unofficial; transcripts can be absent, blocked, or unavailable from cloud-hosted IP addresses. No paid proxy is used. A transcript failure does not fail the whole Scout run.

## 4. Later Intelligence Desk roles

| Role | Responsibility |
|---|---|
| Ecosystem Researcher | Find relevant external technology context for current-week leads or signals. |
| Intelligence Editor | Select and organize backend and research findings into a briefing draft. |
| Fact-Checker and Critic | Check claims, dates, attribution, citations, and clarity before publication. |

These later roles may be coordinated with LangGraph when their workflows are ready. Do not add extra agents or orchestration modules before they have actual work to perform.

## 5. Run and publication behavior

- `POST /api/targets/{id}/intelligence/run` starts a target-scoped Scout run for the last fully closed week.
- `GET /api/targets/{id}/intelligence/runs/{run_id}` returns its status and result.
- `GET /api/targets/{id}/briefings` returns published briefings only. A Scout run does not publish a briefing.
- If no successful Weekly Lead run exists for the week, Scout may still run without leads and should report that context gap. A successful run with zero leads is valid empty context.

## 6. Success criteria

Company Scout should choose useful pages and videos instead of calling every tool in a fixed sequence, cite original source URLs, preserve unknown dates as unknown, and continue when one source is unavailable. Existing daily and weekly analysis must remain unchanged.
