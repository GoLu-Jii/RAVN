import json
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.models import User
from app.db.base import Base
from app.kundali.models import Kundali
from app.targets.models import Target
from app.weekly.config import WeeklyConfig
from app.weekly.models import (
    DetectedSignal,
    SignalDetectionRun,
    WeeklyLead,
    WeeklyLeadRun,
)
from app.weekly.signal_detector import (
    SignalDetectorBusy,
    _fit_signal_input,
    _retrieve_historical_leads,
    run_signal_detection,
)
from app.weekly.signal_extraction import SYSTEM_PROMPT, validate_signal_output


WEEK_START = date(2026, 11, 9)


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


class SignalDetectorTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        owner = User(email="signals@example.invalid", password_hash="x")
        self.session.add(owner)
        self.session.flush()
        self.target = Target(
            user_id=owner.id,
            name="Signal target",
            website_url="https://example.invalid",
            status="ready",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.other_target = Target(
            user_id=owner.id,
            name="Other target",
            website_url="https://other.invalid",
            status="ready",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.session.add_all([self.target, self.other_target])
        self.session.flush()
        self.session.add(Kundali(
            target_id=self.target.id,
            tech_stack={"languages": ["Python"]},
            focus_areas={"themes": ["data infrastructure"]},
            cadence_baseline={"commits": [2, 4]},
            recent_commits={"summary": "steady development"},
            ats_postings=[],
            web_social_status={},
            blog_status={},
        ))
        self.session.commit()
        self.current_run = self.add_weekly_run(self.target.id, WEEK_START)
        self.current_lead = self.add_lead(
            self.current_run,
            "Current lead",
            [1.0] + [0.0] * 255,
        )
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def add_weekly_run(self, target_id, week_start, *, attempt=1, current=True, status="succeeded"):
        run = WeeklyLeadRun(
            target_id=target_id,
            week_start_date=week_start,
            attempt=attempt,
            status=status,
            is_current=current,
            retryable=False,
            input_event_ids=[],
        )
        self.session.add(run)
        self.session.flush()
        return run

    def add_lead(self, run, title, vector, *, index=0, description=None):
        lead = WeeklyLead(
            run_id=run.id,
            target_id=run.target_id,
            week_start_date=run.week_start_date,
            lead_index=index,
            title=title,
            description=description or f"{title} details",
            source_event_ids=[],
            embedding=vector,
            embedding_model_id="voyage-4-lite",
        )
        self.session.add(lead)
        self.session.flush()
        return lead

    def emerging_output(self, current_id=None):
        return {
            "signals": [{
                "title": "Emerging platform investment",
                "description": "Current evidence points to a new platform initiative.",
                "signal_types": ["emerging"],
                "confidence": "medium",
                "confidence_rationale": "Several current leads support the interpretation.",
                "current_lead_ids": [current_id or self.current_lead.id],
                "historical_lead_ids": [],
            }],
            "no_matches_reason": None,
        }

    def run_with(self, content=None, *, llm=None, week_start=WEEK_START):
        fake = llm or FakeLLM(content)
        result = run_signal_detection(
            self.session,
            target_id=self.target.id,
            week_start=week_start,
            config=WeeklyConfig(),
            llm_client=fake,
        )
        return result, fake

    def test_retrieves_only_same_target_previous_twelve_weeks_top_three(self):
        prior_run = self.add_weekly_run(
            self.target.id, WEEK_START - timedelta(weeks=1)
        )
        prior_leads = [
            self.add_lead(
                prior_run, f"Prior {index}", [1.0] + [0.0] * 255, index=index
            )
            for index in range(4)
        ]
        other_run = self.add_weekly_run(
            self.other_target.id, WEEK_START - timedelta(weeks=1)
        )
        self.add_lead(other_run, "Other target", [1.0] + [0.0] * 255)
        too_old_run = self.add_weekly_run(
            self.target.id, WEEK_START - timedelta(weeks=13)
        )
        self.add_lead(too_old_run, "Too old", [1.0] + [0.0] * 255)
        self.session.commit()

        candidates = _retrieve_historical_leads(
            self.session,
            target_id=self.target.id,
            week_start=WEEK_START,
            current_leads=[self.current_lead],
            model_id="voyage-4-lite",
        )

        self.assertEqual(len(candidates), 3)
        self.assertEqual(
            {candidate["id"] for candidate in candidates},
            {lead.id for lead in prior_leads[:3]},
        )
        self.assertTrue(all(candidate["similarity"] == 1.0 for candidate in candidates))

    def test_deduplicates_history_retrieved_for_multiple_current_leads(self):
        second_current = self.add_lead(
            self.current_run,
            "Second current",
            [0.9, 0.1] + [0.0] * 254,
            index=1,
        )
        prior_run = self.add_weekly_run(
            self.target.id, WEEK_START - timedelta(weeks=1)
        )
        prior = self.add_lead(
            prior_run, "Related past lead", [1.0, 0.0] + [0.0] * 254
        )
        self.session.commit()

        candidates = _retrieve_historical_leads(
            self.session,
            target_id=self.target.id,
            week_start=WEEK_START,
            current_leads=[self.current_lead, second_current],
            model_id="voyage-4-lite",
        )

        self.assertEqual([item["id"] for item in candidates], [prior.id])
        self.assertEqual(
            candidates[0]["matched_current_lead_ids"],
            [self.current_lead.id, second_current.id],
        )
        self.assertEqual(candidates[0]["week_start_date"], (WEEK_START - timedelta(weeks=1)).isoformat())

    def test_signal_call_includes_kundali_current_and_history(self):
        prior_run = self.add_weekly_run(
            self.target.id, WEEK_START - timedelta(weeks=1)
        )
        prior = self.add_lead(
            prior_run, "Prior developer automation", [1.0] + [0.0] * 255
        )
        self.session.commit()
        output = {
            "signals": [{
                "title": "Continued developer automation",
                "description": "This week continues a prior automation initiative.",
                "signal_types": ["continuation"],
                "confidence": "high",
                "confidence_rationale": "Related leads occur in both weeks.",
                "current_lead_ids": [self.current_lead.id],
                "historical_lead_ids": [prior.id],
            }],
            "no_matches_reason": None,
        }

        result, llm = self.run_with(output)

        payload = json.loads(llm.calls[0]["messages"][1]["content"])
        self.assertEqual(llm.calls[0]["provider"], "openrouter")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(payload["kundali"]["tech_stack"], {"languages": ["Python"]})
        self.assertEqual(payload["current_leads"][0]["id"], self.current_lead.id)
        self.assertEqual(payload["historical_candidates"][0]["id"], prior.id)
        self.assertEqual(payload["historical_candidates"][0]["week_start_date"], "2026-11-02")
        self.assertAlmostEqual(payload["historical_candidates"][0]["similarity"], 1.0)
        self.assertLess(result["input_size_bytes"], 50000)
        signal = self.session.query(DetectedSignal).one()
        self.assertEqual(signal.historical_lead_ids, [prior.id])

    def test_empty_current_week_still_calls_llm_without_history(self):
        self.current_run.status = "no_events"
        self.session.delete(self.current_lead)
        self.session.commit()
        llm = FakeLLM({
            "signals": [],
            "no_matches_reason": "There are no current weekly leads to analyze.",
        })

        result, _ = self.run_with(llm=llm)

        payload = json.loads(llm.calls[0]["messages"][1]["content"])
        self.assertEqual(payload["current_leads"], [])
        self.assertEqual(payload["historical_candidates"], [])
        self.assertEqual(payload["kundali"]["focus_areas"], {"themes": ["data infrastructure"]})
        self.assertEqual(result["status"], "no_signals")
        self.assertEqual(self.session.query(DetectedSignal).count(), 0)

    def test_input_budget_keeps_baseline_current_and_prioritizes_recent_history(self):
        base = {
            "target": {"id": 1, "name": "Target"},
            "week_start": WEEK_START.isoformat(),
            "kundali": {"profile": "baseline"},
            "current_leads": [{"id": 1, "title": "current", "description": "current details"}],
            "historical_candidates": [],
        }
        recent = {
            "id": 11,
            "week_start_date": (WEEK_START - timedelta(weeks=1)).isoformat(),
            "title": "recent",
            "description": "r" * 100,
            "similarity": 0.2,
            "matched_current_lead_ids": [1],
        }
        older = {
            "id": 12,
            "week_start_date": (WEEK_START - timedelta(weeks=8)).isoformat(),
            "title": "older",
            "description": "o" * 100,
            "similarity": 0.99,
            "matched_current_lead_ids": [1],
        }
        base_size = len(
            (SYSTEM_PROMPT + json.dumps(base, ensure_ascii=False)).encode("utf-8")
        )
        recent_size = len(json.dumps(recent, ensure_ascii=False).encode("utf-8"))
        config = WeeklyConfig(signal_input_byte_budget=base_size + recent_size + 30)

        fitted, estimated = _fit_signal_input(
            {**base, "historical_candidates": [recent, older]},
            config=config,
        )

        self.assertEqual([item["id"] for item in fitted["historical_candidates"]], [11])
        self.assertEqual(fitted["kundali"], base["kundali"])
        self.assertEqual(fitted["current_leads"], base["current_leads"])
        self.assertLessEqual(estimated, config.signal_input_byte_budget)

    def test_rejects_bad_citations_and_historical_claim_without_history(self):
        with self.assertRaises(ValueError):
            validate_signal_output(
                {
                    "signals": [{
                        **self.emerging_output()["signals"][0],
                        "current_lead_ids": [99999],
                    }],
                    "no_matches_reason": None,
                },
                current_lead_ids={self.current_lead.id},
                historical_lead_ids=set(),
                max_signals=8,
            )

        with self.assertRaises(ValueError):
            validate_signal_output(
                {
                    "signals": [{
                        **self.emerging_output()["signals"][0],
                        "signal_types": ["continuation"],
                    }],
                    "no_matches_reason": None,
                },
                current_lead_ids={self.current_lead.id},
                historical_lead_ids=set(),
                max_signals=8,
            )

    def test_failed_rerun_preserves_current_signal_result(self):
        first, _ = self.run_with(self.emerging_output())
        failed, _ = self.run_with(llm=FakeLLM(error=RuntimeError("provider down")))

        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(failed["status"], "failed")
        runs = self.session.query(SignalDetectionRun).order_by(
            SignalDetectionRun.attempt.asc()
        ).all()
        self.assertTrue(runs[0].is_current)
        self.assertFalse(runs[1].is_current)
        self.assertEqual(self.session.query(DetectedSignal).count(), 1)

    def test_successful_rerun_supersedes_previous_signal_set(self):
        first, _ = self.run_with(self.emerging_output())
        second, _ = self.run_with({
            "signals": [],
            "no_matches_reason": "The new analysis found no supported signals.",
        })

        self.assertNotEqual(first["run_id"], second["run_id"])
        current = self.session.query(SignalDetectionRun).filter_by(is_current=True).one()
        self.assertEqual(current.id, second["run_id"])
        self.assertEqual(current.status, "no_signals")
        self.assertEqual(self.session.query(DetectedSignal).count(), 1)

    def test_busy_lock_prevents_overlapping_signal_runs(self):
        from app.weekly.signal_detector import SIGNAL_DETECTOR_LOCK

        SIGNAL_DETECTOR_LOCK.acquire()
        try:
            with self.assertRaises(SignalDetectorBusy):
                run_signal_detection(
                    self.session,
                    target_id=self.target.id,
                    week_start=WEEK_START,
                    llm_client=FakeLLM(self.emerging_output()),
                )
        finally:
            SIGNAL_DETECTOR_LOCK.release()
        self.assertEqual(self.session.query(SignalDetectionRun).count(), 0)


if __name__ == "__main__":
    unittest.main()
