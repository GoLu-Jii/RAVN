import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.llm.clients import LLMClient, LLMResponse
from app.weekly.config import WeeklyConfig
from app.weekly.llm_stage import WeeklyLLMError, run_weekly_llm
from app.weekly.prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_messages


VALID_OUTPUT = {
    "summary": "Hiring and product updates point to increased investment.",
    "data_limitations": ["Blog coverage was partial."],
    "signals": [{
        "title": "Expanding the platform team",
        "description": "A recent role and code activity point to platform work.",
        "sources": ["ats", "github"],
        "cited_event_ids": [12, 13],
        "connection_note": "The role and commits are related in topic.",
    }],
}


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def fake_response(content, finish_reason="stop"):
    return SimpleNamespace(
        content=content,
        provider="groq",
        model="openai/gpt-oss-120b",
        raw_response={
            "choices": [{"finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168},
        },
    )


class WeeklyLLMStageTests(unittest.TestCase):
    def setUp(self):
        self.weekly_input = {
            "metadata": {"target_id": 5, "prompt_version": PROMPT_VERSION},
            "current_week_events": [{"event_id": 12, "date_basis": "first_observed"}],
        }
        self.config = WeeklyConfig(max_signals=2)

    def client(self, responses):
        completions = FakeCompletions(responses)
        return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions

    def test_valid_json_is_parsed_and_metadata_is_returned(self):
        client, calls = self.client([fake_response(json.dumps(VALID_OUTPUT))])
        result = run_weekly_llm(self.weekly_input, client=client, config=self.config)
        self.assertEqual(result["output"]["summary"], VALID_OUTPUT["summary"])
        self.assertEqual(result["prompt_version"], PROMPT_VERSION)
        self.assertEqual(result["provider"], "groq")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["usage"]["total_tokens"], 168)
        self.assertEqual(len(calls.calls), 1)

    def test_invalid_json_is_retried_with_validation_error(self):
        client, calls = self.client([
            fake_response("{broken"),
            fake_response(json.dumps(VALID_OUTPUT)),
        ])
        result = run_weekly_llm(self.weekly_input, client=client, config=self.config)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(calls.calls), 2)
        retry_messages = calls.calls[1]["messages"]
        self.assertIn("JSONDecodeError", retry_messages[-1]["content"])

    def test_invalid_output_fails_after_two_retries(self):
        client, calls = self.client([fake_response("not json") for _ in range(3)])
        with self.assertRaises(WeeklyLLMError) as raised:
            run_weekly_llm(self.weekly_input, client=client, config=self.config)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.attempts, 3)
        self.assertEqual(len(calls.calls), 3)

    def test_schema_errors_are_retried_and_extra_scores_are_rejected(self):
        invalid = dict(VALID_OUTPUT, score=4)
        client, calls = self.client([
            fake_response(json.dumps(invalid)),
            fake_response(json.dumps(VALID_OUTPUT)),
        ])
        result = run_weekly_llm(self.weekly_input, client=client, config=self.config)
        self.assertEqual(result["attempts"], 2)
        self.assertIn("score", calls.calls[1]["messages"][-1]["content"])

    def test_truncated_response_fails_without_parsing_or_retry(self):
        client, calls = self.client([fake_response(json.dumps(VALID_OUTPUT), finish_reason="length")])
        with self.assertRaises(WeeklyLLMError) as raised:
            run_weekly_llm(self.weekly_input, client=client, config=self.config)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(len(calls.calls), 1)

    def test_provider_error_is_retryable_but_not_retried_inside_call(self):
        client, calls = self.client([RuntimeError("429 rate limit")])
        with self.assertRaises(WeeklyLLMError) as raised:
            run_weekly_llm(self.weekly_input, client=client, config=self.config)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(len(calls.calls), 1)

    def test_exact_model_is_requested_and_fallback_is_disabled(self):
        client, calls = self.client([fake_response(json.dumps(VALID_OUTPUT))])
        run_weekly_llm(self.weekly_input, client=client, config=self.config)
        self.assertEqual(calls.calls[0]["model"], "openai/gpt-oss-120b")
        self.assertIs(calls.calls[0]["allow_fallback"], False)
        self.assertIs(calls.calls[0]["no_fallbacks"], True)

    def test_prompt_delimits_input_and_has_first_observed_rules(self):
        messages = build_messages(self.weekly_input)
        self.assertIn("<weekly_input_json>", messages[-1]["content"])
        self.assertIn("</weekly_input_json>", messages[-1]["content"])
        self.assertIn("date_basis=first_observed", SYSTEM_PROMPT)
        self.assertIn("not dated event evidence", SYSTEM_PROMPT)
        self.assertIn("Do not invent event IDs", SYSTEM_PROMPT)

    def test_client_allow_fallback_false_preserves_exact_provider_path(self):
        expected = LLMResponse("{}", "groq", "openai/gpt-oss-120b")
        with (
            patch("app.llm.clients._call_groq", return_value=expected) as groq,
            patch("app.llm.clients._call_gemini", side_effect=AssertionError("fallback called")) as gemini,
            patch("app.llm.clients._call_openrouter", side_effect=AssertionError("fallback called")) as router,
        ):
            response = LLMClient().generate(
                messages=[], model="openai/gpt-oss-120b", allow_fallback=False
            )
        self.assertIs(response, expected)
        groq.assert_called_once()
        gemini.assert_not_called()
        router.assert_not_called()


if __name__ == "__main__":
    unittest.main()
