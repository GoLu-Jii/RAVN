'''
What this function needs to do

Something like build_kundali_profile(target_id, github_url) in kundali/builder.py, doing, in order:

Call list_repos(github_url, limit=N) — get your capped repo subset for this target.
For each repo in that subset, call get_commit_activity, get_recent_commits, and get_languages.
Aggregate across the subset:
tech_stack — sum (or however you decided) the language byte-counts across all repos into one combined dict
cadence_baseline — combine the 52-week commit activity across repos (this is the shape decision I flagged earlier — 
per-repo breakdown vs. summed weekly totals)
recent_shifts — something derived from the 45-day recent commits (e.g. which categories/repos saw unusual recent activity 
relative to baseline — though full "shift detection" logic might be more than you want in this pass)
focus_areas — this one's not GitHub-derivable at all from what you've built; it likely needs the LLM synthesis step your 
IMPLEMENTATION_GUIDE.md mentions (kundali/synthesis.py, "two LLM passes"). Worth deciding: are you doing that now, or 
stubbing focus_areas as null/empty for this pass and coming back to it once the raw-data aggregation works end to end?
Write one Kundali row to the DB via SQLAlchemy, using your existing SessionLocal/get_db pattern from auth.
A real design question before you write this

Partial failure handling. Say a target has 10 repos in its subset, and get_commit_activity returns None for 2 of them 
(still-202-after-retries or some other failure). Does the whole Kundali build fail, or do you skip those 2 and aggregate 
from the other 8? Given your stated "graceful degradation" principle throughout the whole project, skipping-and-continuing
is almost certainly the right call — but you should decide explicitly and write the skip logic on purpose, not have it 
happen accidentally because of how the aggregation loop is structured.

Suggested order to build it in

Given your "write everything, test at the end" style — I'd still suggest writing this one function in two visible stages 
even if you test them together: first the gather-and-aggregate logic (steps 1–3, returning a plain dict, no DB yet) so you 
can print/inspect it standalone, then the DB-write step (step 4) on top. That way if something's wrong, you know immediately 
whether it's a data problem or a DB-write problem.

'''



from app.kundali.builder import list_repos, get_commit_activity, get_languages, get_recent_commits

from fastapi import FastAPI, APIRouter, HTTPException, Depends

from app.db.database import get_db
from app.db.base import Base
from app.kundali.models import Kundali
from app.auth.models import User

from app.auth.routes import get_current_user


def build_kundali(target_id: int, github_url: str):

    repos = list_repos(github_url, limit=10) or []

    tech_stack = {}
    cadence_baseline = {}
    recent_shifts = {}

    for repo in repos:
        owner = repo.get("owner")
        name = repo.get("name")

        if not owner or not name:
            continue 

        langs = get_languages(owner, name) or {}
        activity = get_commit_activity(owner, name) or []
        recent_commits = get_recent_commits(owner, name) or []

        for lang, bytes_count in langs.items():
            tech_stack[lang] = tech_stack.get(lang, 0) + bytes_count    

        for i, week in enumerate(activity[52]):
            cadence_baseline[i] += week.get("total", 0)

        if recent_commits:
            recent_shifts.append({"repo": name, "recent_commits": len(recent_commits)})

    # write to db

    new_kundali = Kundali(
        target_id = target_id,
        tech_stack = tech_stack,    
        cadence_baseline = cadence_baseline,
        recent_shifts = recent_shifts,
        focus_areas = None,
    )

    db = next(get_db())
    db.add(new_kundali)
    db.commit()
    db.refresh(new_kundali)

    return new_kundali


    

