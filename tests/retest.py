from app.db.database import SessionLocal
from app.kundali.models import Kundali
from app.targets.models import Target
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

db = SessionLocal()
try:
    # Query the latest target with a Kundali
    latest_k = db.query(Kundali).order_by(Kundali.id.desc()).first()
    if not latest_k:
        print("No Kundali records found in database.")
    else:
        target = db.query(Target).filter(Target.id == latest_k.target_id).first()
        tname = target.name if target else "unknown"
        print(f"=== Latest Kundali (ID: {latest_k.id}, Target ID: {latest_k.target_id} - '{tname}') ===")
        print(f"Tech Stack ({len(latest_k.tech_stack or {})} langs):", list((latest_k.tech_stack or {}).keys())[:5], "...")
        print(f"Cadence Baseline: {len(latest_k.cadence_baseline or {})} weeks recorded")
        print(f"Recent Commits: {len(latest_k.recent_commits or [])} active repos")
        print(f"ATS Postings: {len(latest_k.ats_postings or [])} jobs tracked")
        blogs = latest_k.blog_status if isinstance(latest_k.blog_status, list) else (latest_k.blog_status or {}).get("articles", [])
        print(f"Blog Articles: {len(blogs or [])} articles cached")
        fa = latest_k.focus_areas or {}
        strat = fa.get("strategic_focus_areas", [])
        print(f"Strategic Themes ({len(strat)}):")
        for s in strat:
            print(f"  * {s.get('theme')} [{s.get('confidence')}]")
finally:
    db.close()