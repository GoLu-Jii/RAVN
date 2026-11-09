import os
import unittest
from datetime import date, datetime, timezone
from uuid import uuid4
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.database import create_database_engine
from app.daily.collectors import collect_target_events
from app.events.models import DailySourceCheckpoint, DailySourceSummary, Event
from app.events.routes import get_daily_summaries, list_daily_events
from app.targets.models import Target


class DatabaseEngineTests(unittest.TestCase):
    def test_sqlite_engine_keeps_working(self):
        engine = create_database_engine("sqlite:///:memory:")
        try:
            with engine.connect() as connection:
                self.assertEqual(connection.execute(text("SELECT 1")).scalar_one(), 1)
        finally:
            engine.dispose()


class SupabasePostgresSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.getenv("RAVN_TEST_POSTGRES_URL"),
        "set RAVN_TEST_POSTGRES_URL to run the opt-in PostgreSQL smoke test",
    )
    def test_pgvector_daily_collection_and_read_routes(self):
        url = os.environ["RAVN_TEST_POSTGRES_URL"]
        engine = create_database_engine(url)
        session = sessionmaker(bind=engine, expire_on_commit=False)()
        user_id = None
        target_id = None
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        try:
            with engine.connect() as connection:
                self.assertEqual(connection.execute(text("SELECT 1")).scalar_one(), 1)
                installed = connection.execute(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
                )).scalar_one()
                self.assertTrue(installed)

            user = User(email=f"weekly-smoke-{uuid4()}@example.invalid", password_hash="smoke")
            session.add(user)
            session.flush()
            user_id = user.id
            target = Target(
                user_id=user.id,
                name="Temporary PostgreSQL smoke target",
                website_url="https://example.invalid",
                github_url="https://github.com/example/project",
                status="ready",
            )
            session.add(target)
            session.commit()
            target_id = target.id

            commit_time = datetime.now(ZoneInfo("Asia/Kolkata")).astimezone(timezone.utc).replace(microsecond=0)
            with patch("app.daily.collectors.get_new_commits", return_value=[{
                "sha": f"smoke-{uuid4()}",
                "repo": "example/project",
                "date": commit_time.isoformat(),
                "message": "PostgreSQL smoke test commit",
                "url": "https://github.com/example/project/commit/smoke",
            }]):
                collect_target_events(session, target, today)

            feed = list_daily_events(
                target_id=target.id,
                source_type="github",
                collection_date=today,
                since=None,
                limit=10,
                offset=0,
                current_user=user,
                db=session,
            )
            summaries = get_daily_summaries(
                target_id=target.id,
                collection_date=today,
                current_user=user,
                db=session,
            )
            self.assertEqual(feed["total"], 1)
            self.assertEqual(feed["items"][0]["source_type"], "github")
            self.assertEqual(len(summaries["sources"]), 4)
            self.assertEqual(next(x for x in summaries["sources"] if x["source_type"] == "github")["event_count"], 1)
        finally:
            if target_id is not None:
                session.query(DailySourceCheckpoint).filter_by(target_id=target_id).delete()
                session.query(DailySourceSummary).filter_by(target_id=target_id).delete()
                session.query(Event).filter_by(target_id=target_id).delete()
                session.query(Target).filter_by(id=target_id).delete()
            if user_id is not None:
                session.query(User).filter_by(id=user_id).delete()
            session.commit()
            session.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
