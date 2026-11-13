import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.kundali.synthesis import _synthesize_focus_areas
from app.targets.onboarding import classify_links, parse_response


class OnboardingLLMApiTests(unittest.TestCase):
    def test_classification_sends_discovered_links_and_parses_categories(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "github_url": "https://github.com/acme",
                "ats_url": None,
                "blog_url": "https://acme.com/blog",
                "web_social_url": None,
            })))],
        )
        completions = Mock()
        completions.create.return_value = response
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        links = [("https://github.com/acme", "GitHub"), ("https://acme.com/blog", "Blog")]

        with patch("app.targets.onboarding.get_llm_client", return_value=client):
            result = classify_links(links)

        self.assertEqual(result["github_url"], "https://github.com/acme")
        self.assertEqual(result["blog_url"], "https://acme.com/blog")
        self.assertIsNone(result["ats_url"])
        request = completions.create.call_args.kwargs
        self.assertEqual(json.loads(request["messages"][1]["content"]), [list(link) for link in links])
        self.assertEqual(request["response_format"], {"type": "json_object"})

    def test_parse_response_accepts_json_fences_and_handles_invalid_json(self):
        expected = {"github_url": None, "ats_url": None, "blog_url": None, "web_social_url": None}
        self.assertEqual(parse_response("```json\n" + json.dumps(expected) + "\n```"), expected)
        self.assertEqual(parse_response("not json"), expected)


class KundaliSynthesisLLMApiTests(unittest.TestCase):
    def test_synthesis_sends_compact_signal_payload_and_accepts_json_response(self):
        answer = {
            "executive_summary": "The company is investing in infrastructure.",
            "strategic_focus_areas": [{"theme": "Infrastructure", "confidence": "medium"}],
        }
        response = SimpleNamespace(content=json.dumps(answer))
        with patch("app.kundali.synthesis.chat_completion", return_value=response) as completion:
            result = _synthesize_focus_areas(
                tech_stack={"Python": 100, "Rust": 50},
                cadence_baseline={"0": 1},
                recent_shifts=[{"repo": "platform", "recent_commits": 3}],
                ats_postings=[{"title": "Platform Engineer"}],
                web_social={"content": "Launch update"},
                blog=[{"title": "Engineering update"}],
            )

        self.assertEqual(result["focus_areas"], ["Infrastructure"])
        self.assertEqual(result["rationale"], answer["executive_summary"])
        call = completion.call_args.kwargs
        self.assertEqual(call["model"], "openai/gpt-oss-120b")
        self.assertTrue(call["no_fallbacks"])
        self.assertEqual(call["response_format"], {"type": "json_object"})
        payload = json.loads(call["messages"][1]["content"])
        self.assertEqual(payload["tech_stack_top8_by_bytes"], {"Python": 100, "Rust": 50})
        self.assertEqual(payload["ats_job_titles"], ["Platform Engineer"])

    def test_provider_failure_keeps_profile_build_nonfatal(self):
        with patch(
            "app.kundali.synthesis.chat_completion",
            side_effect=RuntimeError("provider unavailable"),
        ):
            result = _synthesize_focus_areas(
                tech_stack={},
                cadence_baseline={},
                recent_shifts=[],
                ats_postings=[],
                web_social=None,
                blog=None,
            )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
