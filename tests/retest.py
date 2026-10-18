from app.db.database import SessionLocal
from app.kundali.models import Kundali

db = SessionLocal()
row = db.query(Kundali).filter(Kundali.target_id == 2).first()
print(row.tech_stack)
print(row.cadence_baseline)
print(row.recent_commits)
print(row.ats_postings)
print(row.web_social_status)
print(row.blog_status)
db.close()