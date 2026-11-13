import json
import unittest
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.embeddings.model import EMBEDDING_DIMENSION
from app.events.models import Event, EventEmbedding
from app.targets.models import Target
from app.weekly.config import WEEKLY_CONFIG
from app.weekly.retrieval import retrieve_history, search_similar_events


WEEK_START = date(2026, 11, 9)


def vector(x: float, y: float = 0.0) -> list[float]:
    return [x, y] + [0.0] * (EMBEDDING_DIMENSION - 2)


class WeeklyRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        user = User(email="retrieval-test@example.invalid", password_hash="test")
        self.session.add(user)
        self.session.flush()
        self.target = Target(
            user_id=user.id,
            name="Retrieval target",
            website_url="https://example.invalid",
            status="ready",
        )
        self.other_target = Target(
            user_id=user.id,
            name="Other target",
            website_url="https://other.example.invalid",
            status="ready",
        )
        self.session.add_all([self.target, self.other_target])
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def add_event(
        self,
        source,
        collection_date,
        values,
        *,
        target_id=None,
        baseline=False,
        key=None,
        repo="org/repo",
        date_basis="source",
        model_id="voyage-4-lite",
        prep_version=WEEKLY_CONFIG.prep_version,
    ):
        event = Event(
            target_id=target_id or self.target.id,
            source_type=source,
            source_key=key or f"{source}-{collection_date}-{self.session.query(Event).count()}",
            title=f"Commit in {repo}" if source == "github" else f"{source} item",
            url="https://example.invalid/item",
            raw_content=(json.dumps({"repo": repo, "message": "Search improvements"}) if source == "github" else "Details"),
            one_liner="Search improvements",
            collection_date=collection_date,
            event_date=datetime.combine(collection_date, datetime.min.time(), tzinfo=timezone.utc),
            is_baseline=baseline,
            date_basis=date_basis,
        )
        self.session.add(event)
        self.session.flush()
        self.session.add(EventEmbedding(
            event_id=event.id,
            model_id=model_id,
            dimension=EMBEDDING_DIMENSION,
            prep_version=prep_version,
            text_hash=str(event.id).zfill(64),
            embedding=values,
        ))
        self.session.commit()
        return event

    def test_search_is_target_scoped_and_excludes_current_baseline_and_week_13(self):
        included_week_12 = self.add_event("blog", WEEK_START - timedelta(weeks=12), vector(1.0))
        excluded_week_13 = self.add_event("blog", WEEK_START - timedelta(weeks=13), vector(1.0))
        excluded_baseline = self.add_event("blog", WEEK_START - timedelta(days=1), vector(1.0), baseline=True)
        excluded_other_target = self.add_event(
            "blog", WEEK_START - timedelta(days=2), vector(1.0), target_id=self.other_target.id
        )
        excluded_current = self.add_event("blog", WEEK_START, vector(1.0))

        hits = search_similar_events(
            self.session,
            vector(1.0),
            target_id=self.target.id,
            week_start=WEEK_START,
            limit=20,
        )
        self.assertEqual([hit["event_id"] for hit in hits], [included_week_12.id])
        self.assertNotIn(excluded_week_13.id, [hit["event_id"] for hit in hits])
        self.assertNotIn(excluded_baseline.id, [hit["event_id"] for hit in hits])
        self.assertNotIn(excluded_other_target.id, [hit["event_id"] for hit in hits])
        self.assertNotIn(excluded_current.id, [hit["event_id"] for hit in hits])

    def test_search_ranks_by_similarity_only(self):
        older_more_similar = self.add_event("blog", WEEK_START - timedelta(weeks=4), vector(0.9, 0.435889894))
        newer_less_similar = self.add_event("ats", WEEK_START - timedelta(weeks=1), vector(0.8, 0.6))
        hits = search_similar_events(
            self.session, vector(1.0), target_id=self.target.id,
            week_start=WEEK_START, limit=10,
        )
        self.assertEqual([hit["event_id"] for hit in hits], [older_more_similar.id, newer_less_similar.id])

    def test_merge_uses_max_similarity_and_reports_age_weights_and_week_counts(self):
        self.add_event("github", WEEK_START, vector(1.0), key="current-gh", repo="org/repo")
        self.add_event("github", WEEK_START + timedelta(days=1), vector(0.0, 1.0), key="current-gh-2", repo="org/repo")
        self.add_event("ats", WEEK_START, vector(0.0, 1.0), key="current-ats")
        github_old_2 = self.add_event("github", WEEK_START - timedelta(weeks=2), vector(1.0), key="old-gh-2", repo="org/repo")
        github_old_4 = self.add_event("github", WEEK_START - timedelta(weeks=4), vector(0.8, 0.6), key="old-gh-4", repo="org/repo")
        self.add_event("blog", WEEK_START - timedelta(weeks=4), vector(0.6, 0.8), key="old-blog")

        result = retrieve_history(
            self.session,
            target_id=self.target.id,
            week_start=WEEK_START,
            max_queries=10,
            example_k=5,
            count_k=10,
        )
        self.assertEqual(result["queries_used"], 2)  # one GitHub repo plus one ATS query
        by_id = {item["event_id"]: item for item in result["examples"]}
        self.assertEqual(by_id[github_old_2.id]["similarity"], 1.0)
        self.assertEqual(by_id[github_old_2.id]["age_weeks"], 2)
        self.assertAlmostEqual(by_id[github_old_2.id]["age_weight"], 0.5)
        self.assertEqual(by_id[github_old_4.id]["age_weeks"], 4)
        self.assertAlmostEqual(by_id[github_old_4.id]["age_weight"], 0.25)
        self.assertEqual(result["week_match_counts"][(WEEK_START - timedelta(weeks=2)).isoformat()], 1)
        self.assertEqual(result["week_match_counts"][(WEEK_START - timedelta(weeks=4)).isoformat()], 2)
        github_group = next(group for group in result["github_groups"] if group["week_start"] == (WEEK_START - timedelta(weeks=2)).isoformat())
        self.assertEqual(github_group["repo"], "org/repo")
        self.assertEqual(github_group["event_ids"], [github_old_2.id])

    def test_duplicate_query_hits_merge_with_max_similarity_and_query_ids(self):
        self.add_event("github", WEEK_START, vector(1.0), key="q-gh", repo="org/repo")
        self.add_event("ats", WEEK_START, vector(0.8, 0.6), key="q-ats")
        history = self.add_event("blog", WEEK_START - timedelta(days=3), vector(1.0), key="same-hit", date_basis="first_observed")
        result = retrieve_history(self.session, target_id=self.target.id, week_start=WEEK_START)
        item = next(row for row in result["examples"] if row["event_id"] == history.id)
        self.assertEqual(item["similarity"], 1.0)
        self.assertEqual(len(item["query_event_ids"]), 2)
        self.assertEqual(item["date_basis"], "first_observed")

    def test_configured_model_and_preparation_version_are_required(self):
        current = self.add_event("blog", WEEK_START, vector(1.0))
        old_wrong_model = self.add_event(
            "blog", WEEK_START - timedelta(days=1), vector(1.0),
            model_id="another-model",
        )
        result = retrieve_history(self.session, target_id=self.target.id, week_start=WEEK_START)
        self.assertEqual(result["queries_used"], 1)
        self.assertEqual(result["examples"], [])
        self.assertNotEqual(current.id, old_wrong_model.id)


if __name__ == "__main__":
    unittest.main()
