import json
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.events.models import Event
from app.targets.models import Target
from app.weekly.config import WeeklyConfig
from app.weekly.detector import WeeklyRunBusy, run_weekly_analysis
from app.weekly.lead_extraction import (
    SYSTEM_PROMPT,
    MESSAGE_OVERHEAD_BYTES,
    weekly_lead_input_size_bytes,
)
from app.weekly.models import WeeklyLead, WeeklyLeadRun


WEEK_START = date(2026, 11, 2)


class FakeLLM:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            content=json.dumps(self.content),
            provider="openrouter",
            model="openai/gpt-oss-120b",
        )


class SequencedFakeLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=json.dumps(self.outputs.pop(0)),
            provider="openrouter",
            model="openai/gpt-oss-120b",
        )


class WeeklyDetectorTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        user = User(email="detector@example.invalid", password_hash="x")
        self.session.add(user)
        self.session.flush()
        self.target = Target(
            user_id=user.id,
            name="Detector target",
            website_url="https://example.invalid",
            github_url="https://github.com/org/repo",
            status="ready",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.session.add(self.target)
        self.session.flush()
        self.current_event = self.add_event(
            "current-event", WEEK_START, raw_content="Current week evidence"
        )
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def add_event(self, key, collection_date, *, baseline=False, raw_content=None):
        event = Event(
            target_id=self.target.id,
            source_type="github",
            source_key=key,
            title=key,
            raw_content=raw_content or key,
            one_liner=key,
            collection_date=collection_date,
            event_date=datetime.combine(collection_date, datetime.min.time(), timezone.utc),
            is_baseline=baseline,
            date_basis="collection",
        )
        self.session.add(event)
        self.session.flush()
        return event

    def run_with(self, content, *, embedder=None, llm=None):
        fake_llm = llm or FakeLLM(content)
        result = run_weekly_analysis(
            self.session,
            target_id=self.target.id,
            week_start=WEEK_START,
            config=WeeklyConfig(),
            embedder=embedder,
            llm_client=fake_llm,
        )
        return result, fake_llm

    def test_prompt_uses_description_only_and_prioritizes_within_output_limit(self):
        self.assertIn('"description"', SYSTEM_PROMPT)
        self.assertNotIn('"title"', SYSTEM_PROMPT)
        self.assertIn("highest-priority complete leads that fit", SYSTEM_PROMPT)
        self.assertIn("always finish valid JSON", SYSTEM_PROMPT)

    def test_selects_exact_collection_week_excluding_baselines(self):
        self.add_event("prior-week", WEEK_START - timedelta(days=1))
        self.add_event("last-day", WEEK_START + timedelta(days=6))
        self.add_event("next-week", WEEK_START + timedelta(days=7))
        self.add_event("baseline", WEEK_START + timedelta(days=2), baseline=True)
        self.session.commit()
        llm = FakeLLM({"leads": []})

        result, _ = self.run_with({"leads": []}, llm=llm)

        self.assertEqual(result["status"], "succeeded")
        sent = json.loads(llm.calls[0]["messages"][1]["content"])
        self.assertEqual(
            [event["id"] for event in sent["events"]],
            [self.current_event.id, self.session.query(Event).filter_by(source_key="last-day").one().id],
        )
        self.assertEqual(
            [event["collection_date"] for event in sent["events"]],
            [WEEK_START.isoformat(), (WEEK_START + timedelta(days=6)).isoformat()],
        )

    def test_leads_are_embedded_and_saved_with_week_and_citations(self):
        output = {
            "leads": [{
                "description": "The company is improving its platform.",
                "source_event_ids": [self.current_event.id],
            }]
        }
        embedded_texts = []

        def embedder(texts):
            embedded_texts.extend(texts)
            return [[0.25] * 256]

        result, _ = self.run_with(output, embedder=embedder)

        self.assertEqual(result["llm_response"], json.dumps(output))
        self.assertIsNone(result["error"])
        self.assertEqual(result["lead_count"], 1)
        self.assertEqual(
            embedded_texts,
            ["The company is improving its platform."],
        )
        lead = self.session.query(WeeklyLead).one()
        self.assertEqual(lead.week_start_date, WEEK_START)
        self.assertEqual(lead.description, "The company is improving its platform.")
        self.assertEqual(lead.source_event_ids, [self.current_event.id])
        self.assertEqual(lead.embedding, [0.25] * 256)
        run = self.session.query(WeeklyLeadRun).one()
        self.assertEqual(run.status, "succeeded")
        self.assertTrue(run.is_current)

    def test_large_week_is_batched_and_duplicate_citations_are_merged(self):
        second_event = self.add_event(
            "second-event",
            WEEK_START,
            raw_content="Second week evidence",
        )
        self.session.commit()
        single_event_sizes = [
            weekly_lead_input_size_bytes({
                "week_start": WEEK_START.isoformat(),
                "events": [{
                    "id": event.id,
                    "collection_date": event.collection_date.isoformat(),
                    "source": event.source_type,
                    "raw_content": event.raw_content,
                }],
            })
            for event in (self.current_event, second_event)
        ]
        llm = SequencedFakeLLM([
            {"leads": [{
                "description": "Investment is supported by this week's evidence.",
                "source_event_ids": [self.current_event.id],
            }]},
            {"leads": [{
                "description": "  Investment is supported by this week's evidence.  ",
                "source_event_ids": [second_event.id],
            }]},
        ])

        result = run_weekly_analysis(
            self.session,
            target_id=self.target.id,
            week_start=WEEK_START,
            config=WeeklyConfig(weekly_input_byte_budget=max(single_event_sizes)),
            embedder=lambda _: [[0.1] * 256],
            llm_client=llm,
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["batch_count"], 2)
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(llm.calls[0]["provider"], "openrouter")
        call_event_ids = [
            json.loads(call["messages"][1]["content"])["events"][0]["id"]
            for call in llm.calls
        ]
        self.assertEqual(call_event_ids, [self.current_event.id, second_event.id])
        lead = self.session.query(WeeklyLead).one()
        self.assertCountEqual(
            lead.source_event_ids,
            [self.current_event.id, second_event.id],
        )

    def test_event_larger_than_request_budget_fails_without_calling_llm(self):
        empty_input_size = (
            len(SYSTEM_PROMPT.encode("utf-8"))
            + len(json.dumps({
                "week_start": WEEK_START.isoformat(),
                "events": [],
            }).encode("utf-8"))
            + MESSAGE_OVERHEAD_BYTES
        )
        llm = FakeLLM({"leads": []})

        result = run_weekly_analysis(
            self.session,
            target_id=self.target.id,
            week_start=WEEK_START,
            config=WeeklyConfig(weekly_input_byte_budget=empty_input_size + 1),
            llm_client=llm,
        )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["retryable"])
        self.assertIn("exceeds the configured weekly request byte limit", result["error"])
        self.assertEqual(llm.calls, [])

    def test_empty_week_calls_llm_with_empty_events_and_persists_null(self):
        self.session.query(Event).delete()
        self.session.commit()
        llm = FakeLLM({"leads": None})
        embedder = unittest.mock.Mock()

        result, _ = self.run_with({"leads": None}, llm=llm, embedder=embedder)

        self.assertEqual(result["status"], "no_events")
        sent = json.loads(llm.calls[0]["messages"][1]["content"])
        self.assertEqual(sent["events"], [])
        self.assertEqual(self.session.query(WeeklyLead).count(), 0)
        self.assertIsNone(self.session.query(WeeklyLeadRun).one().output_json["leads"])
        embedder.assert_not_called()

    def test_rejects_citation_outside_selected_week(self):
        response = {
            "leads": [{
                "description": "Not grounded in the selected week.",
                "source_event_ids": [99999],
            }]
        }
        result, _ = self.run_with(response)

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["retryable"])
        self.assertEqual(result["llm_response"], json.dumps(response))
        self.assertIn("outside the selected week", result["error"])
        self.assertEqual(self.session.query(WeeklyLead).count(), 0)
        self.assertFalse(self.session.query(WeeklyLeadRun).one().is_current)

    def test_failed_rerun_does_not_replace_previous_success(self):
        first, _ = self.run_with({
            "leads": [{
                "description": "Evidence from the first run.",
                "source_event_ids": [self.current_event.id],
            }]
        }, embedder=lambda _: [[0.1] * 256])
        failed, _ = self.run_with(
            None,
            llm=FakeLLM(error=RuntimeError("provider unavailable")),
        )

        self.assertEqual(first["status"], "succeeded")
        self.assertTrue(failed["retryable"])
        runs = self.session.query(WeeklyLeadRun).order_by(WeeklyLeadRun.attempt).all()
        self.assertTrue(runs[0].is_current)
        self.assertEqual(runs[1].status, "failed")
        self.assertFalse(runs[1].is_current)
        self.assertEqual(self.session.query(WeeklyLead).count(), 1)

    def test_successful_rerun_supersedes_previous_success(self):
        first, _ = self.run_with({
            "leads": [{
                "description": "Evidence from the first run.",
                "source_event_ids": [self.current_event.id],
            }]
        }, embedder=lambda _: [[0.1] * 256])
        second, _ = self.run_with({"leads": []})

        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(
            self.session.query(WeeklyLeadRun).filter_by(is_current=True).count(),
            1,
        )
        current = self.session.query(WeeklyLeadRun).filter_by(is_current=True).one()
        self.assertEqual(current.id, second["run_id"])
        self.assertEqual(self.session.query(WeeklyLead).count(), 1)

    def test_nonretryable_llm_validation_failure_is_recorded(self):
        response = {"wrong_shape": []}
        result, _ = self.run_with(response)
        run = self.session.query(WeeklyLeadRun).one()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["retryable"])
        self.assertEqual(result["llm_response"], json.dumps(response))
        self.assertIn("invalid JSON or lead data", result["error"])
        self.assertEqual(run.status, "failed")

    def test_malformed_llm_json_is_returned_with_validation_error(self):
        class RawResponseLLM:
            def generate(self, **kwargs):
                return SimpleNamespace(
                    content='{"leads":[{"description":"unfinished',
                    provider="openrouter",
                    model="openai/gpt-oss-120b",
                )

        result, _ = self.run_with(None, llm=RawResponseLLM())

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["llm_response"], '{"leads":[{"description":"unfinished')
        self.assertIn("Unterminated string", result["error"])

    def test_embedding_failure_preserves_llm_output_for_audit(self):
        response = {
            "leads": [{
                "description": "Evidence from the selected week.",
                "source_event_ids": [self.current_event.id],
            }]
        }

        def fail_embedding(_):
            raise RuntimeError("embedding provider unavailable")

        result, llm = self.run_with(response, embedder=fail_embedding)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["retryable"])
        run = self.session.query(WeeklyLeadRun).one()
        self.assertEqual(run.output_json, response)
        self.assertEqual(run.raw_output, json.dumps(response))
        self.assertEqual(run.llm_provider, "openrouter")
        self.assertEqual(len(llm.calls), 1)

    def test_busy_lock_rejects_overlap_without_creating_run(self):
        from app.weekly.detector import WEEKLY_LOCK

        WEEKLY_LOCK.acquire()
        try:
            with self.assertRaises(WeeklyRunBusy):
                run_weekly_analysis(
                    self.session, target_id=self.target.id, week_start=WEEK_START
                )
        finally:
            WEEKLY_LOCK.release()
        self.assertEqual(self.session.query(WeeklyLeadRun).count(), 0)


if __name__ == "__main__":
    unittest.main()
