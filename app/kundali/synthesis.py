import json

from app.kundali.builder import list_repos, get_commit_activity, get_languages, get_recent_commits
from app.daily.fetchers.ats import get_ats_postings
from app.daily.fetchers.socials import get_web_social_content
from app.daily.fetchers.blogs import get_blog_posts
from app.llm import chat_completion

from app.db.database import get_db
from app.kundali.models import Kundali
from app.targets.models import Target
from app.auth.models import User

KUNDALI_SYNTHESIS_MODEL = "openai/gpt-oss-120b"



def _synthesize_focus_areas(
    tech_stack: dict,
    cadence_baseline: dict,
    recent_shifts: list,
    ats_postings: list,
    web_social: dict | None,
    blog: list | None,
) -> dict | None:
    """
    LLM synthesis pass: takes the aggregated raw kundali signals and
    returns a structured dict:
        {
            "focus_areas": ["...", ...],   # top 3-5 investment themes
            "rationale": "..."              # one-paragraph reasoning
        }

    Returns None gracefully if the LLM call fails, so the DB write
    can still proceed with focus_areas=None rather than crashing the
    whole build.
    """
    # Build a compact payload — trim raw data to what's actually signal-bearing
    top_langs = sorted(tech_stack.items(), key=lambda x: x[1], reverse=True)[:8]
    lang_summary = {lang: bytes_count for lang, bytes_count in top_langs}

    active_repos = [
        {"repo": r["repo"], "recent_commits": r["recent_commits"]}
        for r in sorted(recent_shifts, key=lambda x: x["recent_commits"], reverse=True)
    ]

    ats_titles = [p["title"] for p in (ats_postings or [])]

    blog_titles = [p["title"] for p in (blog or [])] if isinstance(blog, list) else []

    social_snippet = None
    if web_social and web_social.get("content"):
        social_snippet = web_social["content"][:400]

    payload = {
        "tech_stack_top8_by_bytes": lang_summary,
        "recent_active_repos_45d": active_repos,
        "ats_job_titles": ats_titles[:30],
        "blog_post_titles": blog_titles[:15],
        "web_social_snippet": social_snippet,
    }

    system_prompt = (
        "You are a competitive intelligence analyst. "
        "Given raw public signals from a tech company's GitHub, job board, blog, and social media, "
        "identify their top 3 to 5 current strategic focus areas. "
        "identify the focus areas by finding the connections between the differnet sources/ signals like github, job boards, blog, social media. try to find connections to establish a reasoning behind the output, don't repeat the same thing again and again"
        "Focus areas should be concrete investment themes (e.g. 'AI/ML infrastructure', "
        "'Mobile SDK expansion', 'Developer tooling'), not vague categories. "
        "Return ONLY valid JSON in this exact shape, no extra text:\n"
        '{"focus_areas": ["...", "..."], "rationale": "one concise paragraph explaining the reasoning"}'
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload)},
    ]

    try:
        response = chat_completion(
            messages=messages,
            model=KUNDALI_SYNTHESIS_MODEL,
            response_format={"type": "json_object"},
            temperature=0.4,
            max_tokens=1024,
            no_fallbacks=True,
        )
        result = json.loads(response.content)
        return result
    except Exception as e:
        print(f"[_synthesize_focus_areas] LLM call failed, continuing with focus_areas=None: {e}")
        return None


def build_kundali(target_id: int, github_url: str | None = None, ats_url: str | None = None, web_social_url: str | None = None, blog_url: str | None = None) -> Kundali:


    # github repo check 
    repos = list_repos(github_url, limit=10) or []

    tech_stack = {}
    cadence_baseline = {}
    recent_shifts = []   # FIX: was {} — you .append() to this later, so it must be a list from the start

    for repo in repos:
        owner = repo.get("owner")
        name = repo.get("repo")   # already fixed by you — matches list_repos' actual key

        if not owner or not name:
            continue

        langs = get_languages(owner, name) or {}
        activity = get_commit_activity(owner, name)   # NOTE: no `or []` here yet — see below
        recent_commits = get_recent_commits(owner, name) or []

        for lang, bytes_count in langs.items():
            tech_stack[lang] = tech_stack.get(lang, 0) + bytes_count

        # FIX: graceful degradation — skip cadence aggregation for this repo
        # if activity failed (None) or came back empty, instead of crashing
        if activity:
            for i, week in enumerate(activity):   # FIX: enumerate(activity), not activity[51]
                cadence_baseline[i] = cadence_baseline.get(i, 0) + week.get("total", 0)
                # FIX: .get(i, 0) default — was cadence_baseline[i] += ... on an empty dict

        if recent_commits:
            recent_shifts.append({"repo": name, "recent_commits": len(recent_commits)})


    # ats platform check 
    postings = get_ats_postings(ats_url) if ats_url else []


    # web social check
    web_social = get_web_social_content(web_social_url) if web_social_url else None


    # blog check
    blog = get_blog_posts(blog_url) if blog_url else None



    # LLM synthesis pass — derives focus_areas from the aggregated signals
    focus_areas = _synthesize_focus_areas(
        tech_stack=tech_stack,
        cadence_baseline=cadence_baseline,
        recent_shifts=recent_shifts,
        ats_postings=postings,
        web_social=web_social,
        blog=blog,
    )


    # write to db
    db_gen = get_db()
    db = next(db_gen)
    try:
        existing = db.query(Kundali).filter(Kundali.target_id == target_id).first()
        if existing:
            existing.tech_stack = tech_stack
            existing.cadence_baseline = cadence_baseline
            existing.recent_commits = recent_shifts
            existing.ats_postings = postings
            existing.web_social_status = web_social
            existing.blog_status = blog
            existing.focus_areas = focus_areas
            target_kundali = existing
        else:
            target_kundali = Kundali(
                target_id=target_id,
                tech_stack=tech_stack,
                cadence_baseline=cadence_baseline,
                recent_commits=recent_shifts,
                ats_postings=postings,
                web_social_status=web_social,
                blog_status=blog,
                focus_areas=focus_areas,
            )
            db.add(target_kundali)

        # Update target status to ready
        target = db.query(Target).filter(Target.id == target_id).first()
        if target:
            target.status = "ready"

        db.commit()
        db.refresh(target_kundali)
    except Exception as e:
        db.rollback()
        target = db.query(Target).filter(Target.id == target_id).first()
        if target:
            target.status = "failed"
            db.commit()
        raise e
    finally:
        db_gen.close()

    return target_kundali
    

