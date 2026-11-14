import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from app.weekly.scoring import enrich_verified_signal, score_signal, signal_types
from app.weekly.verify import verify_signals


WEEK_START = date(2026, 11, 9)


def event(event_id, source="github", day=WEEK_START, *, target_id=1, baseline=False, date_basis="source"):
    return SimpleNamespace(
        id=event_id,
        event_id=event_id,
        target_id=target_id,
        source_type=source,
        collection_date=day,
        is_baseline=baseline,
        date_basis=date_basis,
    )


def candidate(ids, sources=None, **overrides):
    value = {
        "title": "Signal",
        "description": "Evidence-backed description",
        "connection_note": "Connection",
        "sources": sources or ["github"],
        "cited_event_ids": ids,
    }
    value.update(overrides)
    return value


def coverage(configured=True, state="complete"):
    return SimpleNamespace(configured=configured, coverage=state)


class SignalVerificationTests(unittest.TestCase):
    def setUp(self):
        self.current = event(1)
        self.prior = event(2, "blog", WEEK_START - timedelta(days=14), date_basis="first_observed")
        self.events = [self.current, self.prior]

    def verify(self, proposed, events=None):
        return verify_signals(
            proposed,
            self.events if events is None else events,
            target_id=1,
            week_start=WEEK_START,
            history_weeks=12,
        )

    def test_unknown_id_wrong_target_baseline_and_out_of_window_are_rejected(self):
        cases = [
            (candidate([99]), [self.current], "unknown_event_id"),
            (candidate([3], ["ats", "github"]), [event(3, target_id=2)], "wrong_target"),
            (candidate([4]), [event(4, baseline=True)], "baseline_event"),
            (candidate([5]), [event(5, day=WEEK_START - timedelta(weeks=13))], "event_outside_allowed_window"),
            (candidate([6]), [event(6, day=WEEK_START + timedelta(days=7))], "event_outside_allowed_window"),
        ]
        for proposal, events, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = self.verify([proposal], events)
                self.assertEqual(result["verified_signals"], [])
                self.assertIn(expected_reason, result["rejected_signals"][0]["rejection_reasons"])

    def test_source_mismatch_is_rejected_and_extra_claim_is_recorded(self):
        rejected = self.verify([candidate([1], ["ats"])])
        self.assertIn(
            "cited_source_not_claimed",
            rejected["rejected_signals"][0]["rejection_reasons"],
        )
        accepted = self.verify([candidate([1], ["github", "ats"])])
        signal = accepted["verified_signals"][0]
        self.assertEqual(signal["sources"], ["github"])
        self.assertTrue(signal["source_claim_mismatch"])

    def test_no_current_week_citation_is_rejected(self):
        result = self.verify([candidate([2], ["blog"])])
        self.assertIn(
            "no_current_week_citation",
            result["rejected_signals"][0]["rejection_reasons"],
        )

    def test_citations_are_deduplicated_and_first_observed_is_derived(self):
        result = self.verify([candidate([1, 1, 2, 2], ["github", "blog"])])
        signal = result["verified_signals"][0]
        self.assertEqual(signal["cited_event_ids"], [1, 2])
        self.assertEqual(signal["sources"], ["github", "blog"])
        self.assertTrue(signal["uses_first_observed"])
        self.assertFalse(signal["source_claim_mismatch"])


class SignalScoringTests(unittest.TestCase):
    def test_settled_example_all_sources_configured(self):
        states = {
            "github": coverage(True, "complete"),
            "ats": coverage(True, "complete"),
            "blog": coverage(True, "partial"),
            "web_social": coverage(True, "unavailable"),
        }
        result = score_signal([event(1, "github"), event(2, "blog")], states)
        self.assertEqual(result["score"], 2.5)
        self.assertEqual(result["configured_sources"], 4)
        self.assertEqual(result["confirmed"], 2)
        self.assertEqual(result["uncertain"], 1)
        self.assertEqual(result["source_states"]["ats"]["state"], "checked_none")

    def test_settled_example_github_not_configured(self):
        states = {
            "github": coverage(False, "not_configured"),
            "ats": coverage(True, "unavailable"),
            "blog": coverage(True, "partial"),
            "web_social": coverage(True, "complete"),
        }
        result = score_signal([event(1, "ats")], states)
        self.assertEqual(result["score"], 1.5)
        self.assertEqual(result["configured_sources"], 3)
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual(result["uncertain"], 1)
        self.assertEqual(result["source_states"]["github"]["state"], "not_configured")

    def test_settled_example_only_blog_configured(self):
        states = {
            "github": coverage(False, "not_configured"),
            "ats": coverage(False, "not_configured"),
            "blog": coverage(True, "complete"),
            "web_social": coverage(False, "not_configured"),
        }
        result = score_signal([event(1, "blog")], states)
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["configured_sources"], 1)
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual(result["uncertain"], 0)

    def test_llm_proposed_score_and_type_are_ignored(self):
        statuses = {
            source: coverage(source == "github", "complete" if source == "github" else "not_configured")
            for source in ("github", "ats", "blog", "web_social")
        }
        proposed = {
            "title": "Signal",
            "description": "Evidence",
            "connection_note": "Note",
            "sources": ["github"],
            "cited_event_ids": [1],
            "score": 99,
            "signal_types": ["trend"],
            "_verified_events": [event(1)],
        }
        result = enrich_verified_signal(proposed, statuses, week_start=WEEK_START)
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["signal_types"], ["isolated"])


class SignalTypeTests(unittest.TestCase):
    def test_isolated_spike_trend_and_both_labels(self):
        week2 = WEEK_START - timedelta(weeks=2)
        week3 = WEEK_START - timedelta(weeks=3)
        cases = [
            ([event(1)], ["isolated"]),
            ([event(1, "github"), event(2, "ats", WEEK_START + timedelta(days=2))], ["spike"]),
            ([event(1, "github"), event(2, "github", week2), event(3, "blog", week3)], ["trend"]),
            ([event(1, "github"), event(2, "ats", WEEK_START), event(3, "github", week2), event(4, "blog", week3)], ["spike", "trend"]),
        ]
        for citations, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(signal_types(citations, WEEK_START), expected)


if __name__ == "__main__":
    unittest.main()
