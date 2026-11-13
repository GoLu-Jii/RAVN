import json
import os
import unittest
from datetime import date, datetime, timezone
from math import isclose
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pgvector.sqlalchemy import Vector
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.embeddings.model import (
    EMBEDDING_DIMENSION,
    EmbeddingProviderError,
    VOYAGE_MAX_REQUEST_TOKENS,
    _VoyageRateLimiter,
    embed_texts,
    estimate_embedding_tokens,
    split_embedding_batches,
)
from app.embeddings.service import EmbeddingSchemaError, embed_missing_events
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
    def setUp(self):
        self.rate_limiter_patch = patch(
            "app.embeddings.model._rate_limiter",
            SimpleNamespace(
                reserve=Mock(return_value=object()),
                reconcile=Mock(),
            ),
        )
        self.rate_limiter_patch.start()

    def tearDown(self):
        self.rate_limiter_patch.stop()

    def test_hosted_request_uses_document_mode_dimensions_and_normalizes_order(self):
        first = [3.0] + [0.0] * (EMBEDDING_DIMENSION - 1)
        second = [0.0, 4.0] + [0.0] * (EMBEDDING_DIMENSION - 2)
        response = httpx_response({"data": [
            {"index": 1, "embedding": second},
            {"index": 0, "embedding": first},
        ]})
        with (
            patch.dict(os.environ, {"VOYAGE_API_KEY": "unit-test-key"}),
            patch("app.embeddings.model.httpx.post", return_value=response) as post,
        ):
            vectors = embed_texts(["document one", "document two"])

        request = post.call_args.kwargs
        self.assertEqual(request["headers"]["Authorization"], "Bearer unit-test-key")
        self.assertEqual(request["json"]["model"], "voyage-4-lite")
        self.assertEqual(request["json"]["input_type"], "document")
        self.assertEqual(request["json"]["output_dimension"], EMBEDDING_DIMENSION)
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), EMBEDDING_DIMENSION)
        self.assertTrue(isclose(sum(value * value for value in vectors[0]), 1.0))
        self.assertEqual(vectors[0][0], 1.0)
        self.assertEqual(vectors[1][1], 1.0)

    def test_missing_key_and_bad_response_are_clear_errors(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(EmbeddingProviderError, "VOYAGE_API_KEY"):
                embed_texts(["text"])

        response = httpx_response({"data": [{"index": 0, "embedding": [1.0, 2.0]}]})
        with (
            patch.dict(os.environ, {"VOYAGE_API_KEY": "unit-test-key"}),
            patch("app.embeddings.model.httpx.post", return_value=response),
        ):
            with self.assertRaisesRegex(EmbeddingProviderError, "expected 256"):
                embed_texts(["text"])

    def test_non_finite_provider_vectors_are_rejected(self):
        payload = {
            "data": [{"index": 0, "embedding": [float("nan")] + [0.0] * (EMBEDDING_DIMENSION - 1)}]
        }
        import httpx

        response = httpx.Response(
            200,
            content=json.dumps(payload, allow_nan=True),
            headers={"content-type": "application/json"},
            request=httpx.Request("POST", "https://ai.mongodb.com/v1/embeddings"),
        )
        with (
            patch.dict(os.environ, {"VOYAGE_API_KEY": "unit-test-key"}),
            patch("app.embeddings.model.httpx.post", return_value=response),
        ):
            with self.assertRaisesRegex(EmbeddingProviderError, "non-finite"):
                embed_texts(["text"])

    def test_provider_http_error_does_not_expose_key(self):
        response = httpx_response({"message": "unauthorized"}, status_code=401)
        with (
            patch.dict(os.environ, {"VOYAGE_API_KEY": "secret-test-key"}),
            patch("app.embeddings.model.httpx.post", return_value=response),
        ):
            with self.assertRaisesRegex(EmbeddingProviderError, "HTTP 401") as error:
                embed_texts(["text"])
        self.assertNotIn("secret-test-key", str(error.exception))

    def test_provider_auth_error_is_actionable_and_not_retryable(self):
        response = httpx_response({"message": "unauthorized"}, status_code=401)
        with (
            patch.dict(os.environ, {"VOYAGE_API_KEY": "secret-test-key"}),
            patch("app.embeddings.model.httpx.post", return_value=response),
        ):
            with self.assertRaises(EmbeddingProviderError) as error:
                embed_texts(["text"])

        self.assertIn("HTTP 401", str(error.exception))
        self.assertIn("VOYAGE_API_KEY", str(error.exception))
        self.assertFalse(error.exception.retryable)
        self.assertNotIn("secret-test-key", str(error.exception))

    def test_provider_rate_limit_error_is_retryable_and_actionable(self):
        response = httpx_response({"message": "rate limited"}, status_code=429)
        with (
            patch.dict(os.environ, {"VOYAGE_API_KEY": "unit-test-key"}),
            patch("app.embeddings.model.httpx.post", return_value=response),
        ):
            with self.assertRaises(EmbeddingProviderError) as error:
                embed_texts(["text"])

        self.assertIn("rate limit or account quota", str(error.exception))
        self.assertTrue(error.exception.retryable)

    def test_large_texts_are_split_below_safe_request_token_budget(self):
        texts = ["x" * 3000, "y" * 3000, "z" * 3000]
        batches = list(split_embedding_batches(texts))
        self.assertEqual([len(batch) for batch in batches], [2, 1])
        for batch in batches:
            self.assertLessEqual(
                sum(estimate_embedding_tokens(text) for text in batch),
                VOYAGE_MAX_REQUEST_TOKENS,
            )

    def test_embedding_request_splits_large_input_and_merges_vectors(self):
        texts = ["a" * 3000, "b" * 3000, "c" * 3000]
        responses = []
        for batch in split_embedding_batches(texts):
            responses.append(httpx_response({
                "data": [
                    {"index": index, "embedding": [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)}
                    for index, _ in enumerate(batch)
                ],
                "usage": {"total_tokens": len(batch) * 1000},
            }))
        with (
            patch.dict(os.environ, {"VOYAGE_API_KEY": "unit-test-key"}),
            patch("app.embeddings.model.httpx.post", side_effect=responses) as post,
        ):
            vectors = embed_texts(texts)

        self.assertEqual(post.call_count, 2)
        self.assertEqual(len(vectors), len(texts))
        for call in post.call_args_list:
            sent_texts = call.kwargs["json"]["input"]
            self.assertLessEqual(
                sum(estimate_embedding_tokens(text) for text in sent_texts),
                VOYAGE_MAX_REQUEST_TOKENS,
            )

    def test_rate_limiter_enforces_three_requests_per_rolling_minute(self):
        now = [0.0]
        waits = []

        def wait(seconds):
            waits.append(seconds)
            now[0] += seconds

        limiter = _VoyageRateLimiter(clock=lambda: now[0], wait=wait)
        for _ in range(3):
            limiter.reserve(100)
        limiter.reserve(100)

        self.assertGreaterEqual(now[0], 60.0)
        self.assertEqual(len(waits), 1)

    def test_rate_limiter_enforces_token_budget_per_rolling_minute(self):
        now = [0.0]
        waits = []

        def wait(seconds):
            waits.append(seconds)
            now[0] += seconds

        limiter = _VoyageRateLimiter(clock=lambda: now[0], wait=wait)
        limiter.reserve(6000)
        limiter.reserve(3000)
        limiter.reserve(2000)

        self.assertGreaterEqual(now[0], 60.0)
        self.assertEqual(len(waits), 1)

    def test_rate_limiter_reconciles_estimates_with_provider_usage(self):
        now = [0.0]
        waits = []

        def wait(seconds):
            waits.append(seconds)
            now[0] += seconds

        limiter = _VoyageRateLimiter(clock=lambda: now[0], wait=wait)
        reservation = limiter.reserve(7000)
        limiter.reconcile(reservation, 1000)
        limiter.reserve(9000)

        self.assertEqual(waits, [])


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

        def fail(_):
            raise RuntimeError("model unavailable")

        result = embed_missing_events(self.session, target_id=self.target.id, embedder=fail)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.session.query(EventEmbedding).count(), 0)

    def test_provider_failure_report_includes_safe_detail_and_retryability(self):
        self.add_event()

        def fail(_):
            raise EmbeddingProviderError(
                "Voyage embeddings request failed with HTTP 401; check VOYAGE_API_KEY.",
                retryable=False,
            )

        result = embed_missing_events(self.session, target_id=self.target.id, embedder=fail)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_type"], "EmbeddingProviderError")
        self.assertIn("HTTP 401", result["error_message"])
        self.assertFalse(result["retryable"])
        self.assertEqual(self.session.query(EventEmbedding).count(), 0)

    def test_token_sized_batches_commit_and_resume_without_reembedding(self):
        for index in range(11):
            event = self.add_event(str(index), "word " * 300)
            event.source_type = "blog"
            self.session.commit()
        failed_calls = []

        def fail_second_batch(texts):
            failed_calls.append(list(texts))
            if len(failed_calls) == 2:
                raise EmbeddingProviderError("temporary rate limit", retryable=True)
            return self.fake_embedder([])(texts)

        partial = embed_missing_events(
            self.session,
            target_id=self.target.id,
            embedder=fail_second_batch,
        )

        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["embedded"], len(failed_calls[0]))
        self.assertEqual(self.session.query(EventEmbedding).count(), len(failed_calls[0]))
        self.assertTrue(partial["retryable"])

        resumed_calls = []
        resumed = embed_missing_events(
            self.session,
            target_id=self.target.id,
            embedder=self.fake_embedder(resumed_calls),
        )
        self.assertEqual(resumed["status"], "success")
        self.assertEqual(sum(map(len, resumed_calls)), 11 - len(failed_calls[0]))
        self.assertEqual(self.session.query(EventEmbedding).count(), 11)

    def test_stale_postgres_vector_schema_fails_before_hosted_request(self):
        self.add_event()
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        with (
            patch.object(self.session, "get_bind", return_value=bind),
            patch(
                "app.embeddings.service.inspect",
                return_value=SimpleNamespace(
                    get_columns=lambda _: [
                        {"name": "embedding", "type": Vector(384)}
                    ]
                ),
            ),
        ):
            result = embed_missing_events(
                self.session,
                target_id=self.target.id,
                embedder=self.fail,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_type"], EmbeddingSchemaError.__name__)
        self.assertIn("vector(384)", result["error_message"])
        self.assertIn("alembic upgrade head", result["error_message"])
        self.assertEqual(self.session.query(EventEmbedding).count(), 0)

    def test_non_finite_custom_embedding_is_not_persisted(self):
        self.add_event()
        vector = [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)
        vector[-1] = float("inf")
        result = embed_missing_events(
            self.session,
            target_id=self.target.id,
            embedder=lambda _: [vector],
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_type"], "ValueError")
        self.assertEqual(self.session.query(EventEmbedding).count(), 0)

    def test_baseline_events_are_not_embedded(self):
        event = self.add_event()
        event.is_baseline = True
        self.session.commit()
        result = embed_missing_events(self.session, target_id=self.target.id, embedder=lambda _: self.fail("should not embed"))
        self.assertEqual(result["status"], "no_work")
        self.assertEqual(self.session.query(EventEmbedding).count(), 0)


def httpx_response(payload, status_code=200):
    import httpx

    return httpx.Response(status_code, json=payload, request=httpx.Request("POST", "https://ai.mongodb.com/v1/embeddings"))


@unittest.skipUnless(
    os.getenv("RAVN_TEST_REAL_EMBEDDINGS") == "1",
    "set RAVN_TEST_REAL_EMBEDDINGS=1 to call the hosted Voyage API and run a semantic check",
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
