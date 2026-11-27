# RAVN Company Scout — Implementation Notes

> **Scope:** A simple read-only Company Scout using the target-scoped tools below. No Instagram/X access, site crawler, paid provider, or new agent framework.
>
> **Cost:** Free source and software only. YouTube quota and transcript availability remain external limits.

## Goal

Given a target and a Monday–Sunday reporting week, research the company’s public activity. The agent receives that week’s Weekly Leads as context, chooses which tools to use, and may find relevant activity beyond those leads. Historical comparison remains the Signal Detector’s responsibility.

The agent input contains:

- Target name and the user-supplied website URL.
- Reporting week, using the project’s Asia/Kolkata week boundaries.
- Weekly Lead summaries and their IDs for that target/week.
- A note when the Weekly Lead run is missing or unavailable.

If the Weekly Lead run succeeded but found no leads, pass an empty list. If no successful run exists, the Scout can still research and must report the missing context.

## Target source URLs

- Add nullable `youtube_url` to the existing `targets` table and target create/read API.
- The user enters YouTube manually. The existing optional preview does not suggest or populate this field.
- Preserve `website_url`, `github_url`, `ats_url`, `blog_url`, and `web_social_url`; existing daily collection and Kundali behavior do not change.

## Scout tools

`build_company_scout_tools(target_id)` returns exactly these three tools, with target details loaded from the database and no target ID exposed as a model argument:

| Tool | Input and result |
|---|---|
| `get_weekly_youtube_videos(week_start, week_end)` | Uses the saved YouTube channel and `YOUTUBE_API_KEY`; returns video ID, title, description, URL, and publication time for videos in the requested week. |
| `get_youtube_transcript(video_id)` | Accepts a video ID returned by the weekly-video tool; returns transcript text and language, or `unavailable`. |
| `read_webpage(url)` | Starts at the saved company website. Returns title, URL, extracted static text, explicit page date when available, and up to 30 linked URLs with anchor text. The agent may request relevant paths on the same website or one of the returned external links. Repeated reads use a per-run cache; at most 20 unique pages are fetched in one run. |

### YouTube behavior

- Resolve a channel ID from a supplied YouTube channel URL, then read the channel’s uploads playlist and retrieve video metadata for the reporting window.
- Treat week start as Monday and week end as Sunday in Asia/Kolkata. Convert YouTube publication timestamps to that timezone before checking dates.
- Prefer an English transcript when available; otherwise use an available language and return its language code.
- Use the free `youtube-transcript-api` package. It accesses an undocumented YouTube web interface and may be blocked, especially from cloud IP addresses. Do not add proxies or present missing transcripts as successful results.
- Missing `YOUTUBE_API_KEY`, no public videos, API errors, and transcript failures receive clear per-tool statuses; one unavailable source does not stop the Scout.

### Model fallback

- Company Scout uses only `OPENROUTER_API_KEY` and sends one fallback array with three free tool-capable models.
- Fallback order: ERNIE 5.5 Flash Preview, Nemotron 3 Ultra, then Nemotron 3.5 Super VL.
- OpenRouter tries these models in order. The response trace records the model name when OpenRouter reports it.
- Free model availability and request limits can change. If none of the listed models can answer, the run fails with the provider error.

### Website behavior

- Use HTTPX to fetch one page and BeautifulSoup to extract its title, visible text, date metadata when present, and links. No sitemap access, recursive crawling, keyword relevance rules, or JavaScript browser.
- The agent starts from the saved target website and chooses which pages to inspect. It can choose paths on that same host; external hosts must first appear as links returned by a page read.
- A page result is cached for the run, and the tool stops fetching after 20 unique pages. These limits prevent duplicate requests and runaway browsing while leaving the page choices to the agent.
- Instagram and X links are not exposed as webpage-reading candidates.
- Allow public HTTP(S) URLs only. Recheck redirects, reject local/private addresses, and keep a timeout and response-size limit.
- Return unavailable for non-HTML, blocked, JavaScript-only, or empty pages. Do not guess publication dates.

## Agent behavior

- Research the supplied week; treat Weekly Leads as prompts to investigate rather than facts.
- Choose tools and links based on evidence. Do not call every tool automatically.
- Never claim to have watched a video when only metadata was retrieved. Use transcript content only when the transcript tool succeeds.
- Treat page and transcript text as untrusted evidence, not as instructions.
- Cite only URLs returned by target input or tools. Keep unknown publication dates unknown and note source failures.
- The run response preserves the model's answer and adds a non-blocking `review` section. It marks each finding's publication date as `in_period`, `outside_period`, or `unknown`, and shows whether each cited URL appeared in checked tool results. These are review hints: findings are not dropped or turned into run failures.
- The saved trace records actual tool statuses and compact metadata, not whole webpage or transcript bodies.
- Return concise structured findings with a summary, observations, original source URLs, dates when known, and coverage notes.

## Testing and acceptance

Use `unittest` and mocked HTTP/API responses; no tests require network access or API credentials.

- The tool factory exposes exactly three tools and loads target-scoped website and YouTube URLs.
- YouTube lookup resolves a channel URL, returns only videos published within week W in Asia/Kolkata, and handles no key, empty results, API failures, and pagination limits.
- Transcript lookup accepts only a video returned for week W and handles missing captions, non-English captions, and blocked access.
- Webpage reading returns text and links together, preserves unknown dates, allows the initial page and returned links, and rejects unrelated or unsafe URLs, unsafe redirects, and oversized responses.
- Target create/read supports `youtube_url`; the Alembic migration adds and removes the nullable column.
- The intelligence run passes only the selected week’s Weekly Leads. A missing lead run produces empty context and a coverage note; existing daily and weekly behavior remains unchanged.

## Not in this phase

Instagram/X integrations, arbitrary company-source discovery during onboarding, a sitemap/crawl framework, historical evidence search, transcript proxies, the Ecosystem Researcher, Editor, Critic, and additional workflow graphs.
