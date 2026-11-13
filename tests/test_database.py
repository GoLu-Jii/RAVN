import os
import unittest
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.database import create_database_engine
from app.daily.collectors import collect_target_events
from app.events.models import DailySourceCheckpoint, DailySourceSummary, Event, EventEmbedding
from app.events.routes import get_daily_summaries, list_daily_events
from app.embeddings.model import EMBEDDING_DIMENSION
from app.targets.models import Target
from app.weekly.retrieval import search_similar_events
from app.weekly.windows import week_start_for
from app.weekly.models import WeeklyResult, WeeklyRun, WeeklySignal
from app.weekly.repository import complete_run, create_run


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
                ats_url="https://careers.example.invalid/jobs",
                status="ready",
            )
            session.add(target)
            session.commit()
            target_id = target.id

            commit_time = datetime.now(ZoneInfo("Asia/Kolkata")).astimezone(timezone.utc).replace(microsecond=0)
            with (
                patch("app.daily.collectors.get_new_commits", return_value=[{
                    "sha": f"smoke-{uuid4()}",
                    "repo": "example/project",
                    "date": commit_time.isoformat(),
                    "message": "PostgreSQL smoke test commit",
                    "url": "https://github.com/example/project/commit/smoke",
                }]),
                patch("app.daily.collectors.get_ats_postings", return_value=[
                    {
                        "title": "Smoke role with unresolved date", "location": None,
                        "url": "https://careers.example.invalid/jobs/unresolved", "posted_at": None,
                        "date_state": "not_checked", "date_check_reason": "failed",
                    },
                    {
                        "title": "Smoke role with no source date", "location": None,
                        "url": "https://careers.example.invalid/jobs/undated", "posted_at": None,
                        "date_state": "undated",
                    },
                ]),
            ):
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
            ats_summary = next(x for x in summaries["sources"] if x["source_type"] == "ats")
            self.assertEqual(ats_summary["candidates_total"], 2)
            self.assertEqual(ats_summary["failed_items"], 1)
            self.assertEqual(ats_summary["not_checked_items"], 1)

            event = session.query(Event).filter_by(
                target_id=target.id, source_type="github"
            ).one()
            vector = [1.0] + [0.0] * 383
            session.add(EventEmbedding(
                event_id=event.id,
                model_id="smoke-model",
                dimension=EMBEDDING_DIMENSION,
                prep_version="smoke-v1",
                text_hash="0" * 64,
                embedding=vector,
            ))
            session.commit()
            stored = session.query(EventEmbedding).filter_by(event_id=event.id).one()
            self.assertEqual(len(stored.embedding), EMBEDDING_DIMENSION)
            self.assertEqual(round(stored.embedding[0], 5), 1.0)

            week_start = week_start_for(today)
            historical = []
            for index, values in enumerate(([1.0, 0.0], [0.8, 0.6]), start=1):
                historical_day = week_start - timedelta(days=index)
                historical_event = Event(
                    target_id=target.id,
                    source_type="github",
                    source_key=f"retrieval-smoke-{uuid4()}",
                    title="Commit in example/project",
                    raw_content='{"repo":"example/project","message":"retrieval smoke"}',
                    one_liner="retrieval smoke",
                    collection_date=historical_day,
                    event_date=datetime.combine(historical_day, datetime.min.time(), tzinfo=timezone.utc),
                    is_baseline=False,
                    date_basis="source",
                )
                session.add(historical_event)
                session.flush()
                session.add(EventEmbedding(
                    event_id=historical_event.id,
                    model_id="smoke-model",
                    dimension=EMBEDDING_DIMENSION,
                    prep_version="smoke-v1",
                    text_hash=str(uuid4()).replace("-", "") * 2,
                    embedding=values + [0.0] * (EMBEDDING_DIMENSION - len(values)),
                ))
                historical.append(historical_event)
            session.commit()

            query_vector = [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)
            postgres_hits = search_similar_events(
                session,
                query_vector,
                target_id=target.id,
                week_start=week_start,
                limit=10,
                model_id="smoke-model",
                prep_version="smoke-v1",
                backend="postgresql",
            )
            python_hits = search_similar_events(
                session,
                query_vector,
                target_id=target.id,
                week_start=week_start,
                limit=10,
                model_id="smoke-model",
                prep_version="smoke-v1",
                backend="python",
            )
            self.assertEqual(
                [hit["event_id"] for hit in postgres_hits],
                [hit["event_id"] for hit in python_hits],
            )

            run = create_run(
                session,
                target_id=target.id,
                week_start=week_start,
                attempt=1,
            )
            result = complete_run(
                session,
                run,
                weekly_input={"metadata": {"prompt_version": "smoke-v1", "history_weeks_available": 1}},
                llm_result={
                    "output": {"summary": "PostgreSQL weekly smoke", "data_limitations": []},
                    "raw_output": '{"summary":"PostgreSQL weekly smoke"}',
                    "provider": "fake",
                    "model": "smoke-model",
                    "prompt_version": "smoke-v1",
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                },
                source_states={"github": {"coverage": "complete"}},
                exact_counts=[{"week_start": week_start.isoformat(), "sources": {"github": 1}}],
                signals=[{
                    "title": "Smoke signal",
                    "description": "Temporary smoke evidence.",
                    "connection_note": "One cited source.",
                    "sources": ["github"],
                    "claimed_sources": ["github", "ats"],
                    "source_claim_mismatch": True,
                    "signal_types": ["isolated"],
                    "score": 1.0,
                    "configured_sources": 2,
                    "confirmed": 1,
                    "uncertain": 0,
                    "source_states": {"github": {"state": "supported", "score": 1.0}},
                    "cited_event_ids": [event.id],
                    "uses_first_observed": False,
                }],
            )
            self.assertEqual(result.summary, "PostgreSQL weekly smoke")
            self.assertEqual(session.query(WeeklyRun).filter_by(id=run.id).one().status, "succeeded")
            stored_signal = session.query(WeeklySignal).filter_by(result_id=result.id).one()
            self.assertEqual(stored_signal.claimed_sources, ["github", "ats"])
            self.assertTrue(stored_signal.source_claim_mismatch)
        finally:
            if target_id is not None:
                weekly_results = session.query(WeeklyResult).filter_by(target_id=target_id).all()
                for weekly_result in weekly_results:
                    session.query(WeeklySignal).filter_by(result_id=weekly_result.id).delete()
                session.query(WeeklyResult).filter_by(target_id=target_id).delete()
                session.query(WeeklyRun).filter_by(target_id=target_id).delete()
                session.query(DailySourceCheckpoint).filter_by(target_id=target_id).delete()
                session.query(DailySourceSummary).filter_by(target_id=target_id).delete()
                session.query(EventEmbedding).filter(
                    EventEmbedding.event_id.in_(
                        session.query(Event.id).filter_by(target_id=target_id)
                    )
                ).delete(synchronize_session=False)
                session.query(Event).filter_by(target_id=target_id).delete()
                session.query(Target).filter_by(id=target_id).delete()
            if user_id is not None:
                session.query(User).filter_by(id=user_id).delete()
            session.commit()
            session.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
