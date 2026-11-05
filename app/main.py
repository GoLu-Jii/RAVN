from fastapi import FastAPI
from app.auth.routes import router as auth_router
from app.targets.routes import router as targets_router
from app.events.routes import router as daily_router
from app.scheduler.jobs import create_scheduler

app = FastAPI(
    title="RAVN Competitive Intelligence API",
    description="""
### Recommended Testing Order:
1. **POST `/auth/login`** &rarr; Get bearer token. Click **Authorize** (top-right lock icon).
2. **POST `/targets/preview`** &rarr; Enter company domain (e.g. `stripe.com`). Returns candidate URLs.
3. **POST `/targets?sync=true`** &rarr; Confirm target with URLs. `sync=true` builds Kundali immediately.
4. **GET `/targets/{target_id}/kundali/raw`** &rarr; Inspect raw uninterpreted signals (GitHub, ATS jobs, Blog, Social).
5. **GET `/targets/{target_id}/kundali/synthesis`** &rarr; Inspect deep strategic LLM intelligence dossier.
6. **POST `/targets/{target_id}/kundali/build`** &rarr; Rebuild Kundali anytime using saved URLs. **No JSON body required!**
7. **POST `/targets/{target_id}/daily/collect`** &rarr; Scrape all configured daily sources with the target ID only.
8. **GET `/targets/{target_id}/daily/events`** &rarr; Read the stored feed; source and date filters are optional.
9. **GET `/targets/{target_id}/daily/summaries`** &rarr; Read the four stored source highlights for a date.
    """,
    version="1.0.0",
)


@app.on_event("startup")
def print_endpoint_guide():
    print("""
========================================================================
                 RAVN API -- Testing Flow Summary
========================================================================
[1] POST /auth/login                        -> Get bearer token & authorize
[2] POST /targets/preview                   -> Preview candidate links (e.g. stripe.com)
[3] POST /targets?sync=true                 -> Save target & build Day-0 Kundali
[4] GET  /targets/{target_id}/kundali/raw   -> View raw signals (GitHub, jobs, blog, social)
[5] GET  /targets/{target_id}/kundali/synthesis -> View deep LLM intelligence dossier
[6] POST /targets/{target_id}/kundali/build -> Rebuild Kundali (0 body needed!)
[7] POST /targets/{target_id}/daily/collect -> Collect all configured daily sources (target ID only)
[8] GET  /targets/{target_id}/daily/events -> Read stored event feed (optional filters)
[9] GET  /targets/{target_id}/daily/summaries -> Read daily highlights grouped by source and date
Interactive Swagger UI: http://localhost:8000/docs
========================================================================
""")


@app.on_event("startup")
def start_daily_scheduler():
    scheduler = create_scheduler()
    scheduler.start()
    app.state.daily_scheduler = scheduler


@app.on_event("shutdown")
def stop_daily_scheduler():
    scheduler = getattr(app.state, "daily_scheduler", None)
    if scheduler:
        scheduler.shutdown(wait=False)


app.include_router(auth_router, prefix="/auth", tags=["1. Authentication"])
app.include_router(targets_router, prefix="/targets", tags=["2. Targets & Kundali"])
app.include_router(daily_router)
