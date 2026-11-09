import os
import unittest
from datetime import date, datetime, timezone
from math import isclose
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.embeddings.model import EMBEDDING_DIMENSION, _load_model, embed_texts
from app.embeddings.service import embed_missing_events
from app.embeddings.text import MAX_WORDS, build_embedding_text
from app.events.models import Event, EventEmbedding
from app.targets.models import Target


class EmbeddingTextTests(unittest.TestCase):
    def test_source_text_is_deterministic_and_word_capped(self):
        event = {
            "source_type": "blog",
            "title": "  New   product ",
            "raw_content": "word " * (MAX_WORDS + 20),
        }
        first = build_embedding_text(event)
        self.assertEqual(first, build_embedding_text(event))
        self.assertLessEqual(len(first.split()), MAX_WORDS)
        self.assertTrue(first.startswith("Article title: New product"))

    def test_source_specific_preparation(self):
        cases = [
            ({"source_type": "github", "title": "Commit in org/repo", "raw_content": '{"message":"Fix search"}'}, "Repository: org/repo"),
            ({"source_type": "ats", "title": "Engineer", "raw_content": "Location: Remote\nDescription: Build systems"}, "Location: Remote"),
            ({"source_type": "blog", "title": "Launch", "raw_content": "Opening paragraph"}, "Opening: Opening paragraph"),
            ({"source_type": "web_social", "title": "Update", "url": "https://example.com", "raw_content": "Changed copy"}, "Changed text: Changed copy"),
        ]
        for event, expected in cases:
            with self.subTest(source=event["source_type"]):
                self.assertIn(expected, build_embedding_text(event))


class EmbeddingModelTests(unittest.TestCase):
    def test_model_is_lazy_and_vectors_are_normalized(self):
        _load_model.cache_clear()
        self.assertEqual(_load_model.cache_info().currsize, 0)

        class FakeModel:
            def encode(self, texts, **kwargs):
                return [[3.0] + [0.0] * (EMBEDDING_DIMENSION - 1) for _ in texts]

        with patch("app.embeddings.model._load_model", return_value=FakeModel()) as load:
            vectors = embed_texts(["one", "two"])
        load.assert_called_once()
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), EMBEDDING_DIMENSION)
        self.assertTrue(isclose(sum(value * value for value in vectors[0]), 1.0))

    def test_invalid_embedding_dimensions_are_rejected(self):
        class BadModel:
            def encode(self, texts, **kwargs):
                return [[1.0, 2.0] for _ in texts]

        with patch("app.embeddings.model._load_model", return_value=BadModel()):
            with self.assertRaises(ValueError):
                embed_texts(["text"])


class EmbeddingServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        user = User(email="embedding-test@example.invalid", password_hash="test")
        self.session.add(user)
        self.session.flush()
        self.target = Target(
            user_id=user.id,
            name="Embedding test target",
            website_url="https://example.invalid",
            status="ready",
        )
        self.session.add(self.target)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def add_event(self, key="one", raw="A commit message"):
        event = Event(
            target_id=self.target.id,
            source_type="github",
            source_key=key,
            title="Commit in org/repo",
            raw_content=raw,
            one_liner="Commit",
            collection_date=date(2026, 11, 1),
            event_date=datetime(2026, 11, 1, tzinfo=timezone.utc),
            is_baseline=False,
        )
        self.session.add(event)
        self.session.commit()
        return event

    @staticmethod
    def fake_embedder(calls):
        def embed(texts):
            calls.append(list(texts))
            return [[1.0] + [0.0] * (EMBEDDING_DIMENSION - 1) for _ in texts]
        return embed

    def test_embeds_only_missing_rows_and_skips_unchanged_hash(self):
        event1 = self.add_event("one")
        event2 = self.add_event("two", "Another commit")
        calls = []
        first = embed_missing_events(
            self.session,
            target_id=self.target.id,
            embedder=self.fake_embedder(calls),
        )
        self.assertEqual(first["embedded"], 2)
        self.assertEqual(len(calls), 1)

        calls.clear()
        second = embed_missing_events(
            self.session,
            target_id=self.target.id,
            embedder=self.fake_embedder(calls),
        )
        self.assertEqual(second["status"], "no_work")
        self.assertEqual(second["already_current"], 2)
        self.assertEqual(calls, [])

        event2.one_liner = "Changed commit text"
        self.session.commit()
        changed = embed_missing_events(
            self.session,
            target_id=self.target.id,
            embedder=self.fake_embedder(calls),
        )
        self.assertEqual(changed["embedded"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.session.query(EventEmbedding).count(), 2)
        self.assertEqual(event1.id, self.session.query(EventEmbedding).filter_by(event_id=event1.id).one().event_id)

    def test_embedding_failure_leaves_event_without_embedding(self):
        self.add_event()

        def fail(_texts):
            raise RuntimeError("model unavailable")

        result = embed_missing_events(self.session, target_id=self.target.id, embedder=fail)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.session.query(EventEmbedding).count(), 0)

    def test_baseline_events_are_not_embedded(self):
        event = self.add_event()
        event.is_baseline = True
        self.session.commit()
        result = embed_missing_events(self.session, target_id=self.target.id, embedder=lambda _: self.fail("should not embed"))
        self.assertEqual(result["status"], "no_work")
        self.assertEqual(self.session.query(EventEmbedding).count(), 0)


@unittest.skipUnless(
    os.getenv("RAVN_TEST_REAL_EMBEDDINGS") == "1",
    "set RAVN_TEST_REAL_EMBEDDINGS=1 to download/load the local model and run semantic check",
)
class RealEmbeddingTests(unittest.TestCase):
    def test_related_sentences_have_higher_cosine_similarity(self):
        from app.embeddings.model import embed_texts

        first, related, unrelated = embed_texts([
            "The company is hiring engineers to build virtual reality products.",
            "The firm seeks developers for immersive VR software and headsets.",
            "The office cafeteria changed its lunch menu this week.",
        ])
        related_similarity = sum(a * b for a, b in zip(first, related))
        unrelated_similarity = sum(a * b for a, b in zip(first, unrelated))
        self.assertGreater(related_similarity, unrelated_similarity)


if __name__ == "__main__":
    unittest.main()
