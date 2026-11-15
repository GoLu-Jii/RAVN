import json
import os
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from app.llm import clients


class LLMProviderRequestTests(unittest.TestCase):
    def test_explicit_openrouter_provider_uses_only_openrouter(self):
        expected = clients.LLMResponse("{}", "openrouter", "openai/gpt-oss-120b")
        with (
            patch("app.llm.clients._call_openrouter", return_value=expected) as openrouter,
            patch("app.llm.clients._call_groq", side_effect=AssertionError("Groq called")) as groq,
            patch("app.llm.clients._call_gemini", side_effect=AssertionError("Gemini called")) as gemini,
        ):
            response = clients.LLMClient().generate(
                messages=[],
                model="openai/gpt-oss-120b",
                provider="openrouter",
                allow_fallback=False,
                no_fallbacks=True,
            )

        self.assertIs(response, expected)
        openrouter.assert_called_once()
        self.assertEqual(openrouter.call_args.kwargs["model"], "openai/gpt-oss-120b")
        groq.assert_not_called()
        gemini.assert_not_called()

    def test_bad_request_is_not_retryable_and_rate_limit_is_retryable(self):
        class ProviderError(Exception):
            def __init__(self, status_code):
                super().__init__(f"provider returned {status_code}")
                self.status_code = status_code

        for status_code, retryable in ((400, False), (429, True)):
            with self.subTest(status_code=status_code):
                with patch(
                    "app.llm.clients._call_groq",
                    side_effect=ProviderError(status_code),
                ):
                    with self.assertRaises(clients.LLMError) as raised:
                        clients.LLMClient().generate(
                            messages=[],
                            model="openai/gpt-oss-120b",
                            allow_fallback=False,
                        )
                self.assertEqual(raised.exception.retryable, retryable)

    def test_openrouter_request_and_response_contract(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "model": "vendor/model",
            "choices": [{"message": {"content": "  answer  "}}],
        }
        with (
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch("app.llm.clients.requests.post", return_value=response) as post,
        ):
            result = clients._call_openrouter(
                [{"role": "user", "content": "question"}],
                model="vendor/model",
                response_format={"type": "json_object"},
            )

        self.assertEqual(result.content, "answer")
        self.assertEqual(result.provider, "openrouter")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "vendor/model")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "question"}])
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(post.call_args.args[0], "https://openrouter.ai/api/v1/chat/completions")

    def test_openrouter_non_success_response_raises_provider_error(self):
        response = Mock(status_code=429, text="rate limit")
        with (
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch("app.llm.clients.requests.post", return_value=response),
        ):
            with self.assertRaisesRegex(clients.LLMProviderError, "HTTP 429"):
                clients._call_openrouter([{"role": "user", "content": "question"}])

    def test_openrouter_bad_request_is_not_retryable(self):
        response = Mock(status_code=400, text="invalid request")
        with (
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch("app.llm.clients.requests.post", return_value=response),
        ):
            with self.assertRaises(clients.LLMError) as raised:
                clients.LLMClient().generate(
                    messages=[{"role": "user", "content": "question"}],
                    model="vendor/model",
                    provider="openrouter",
                    allow_fallback=False,
                    no_fallbacks=True,
                )

        self.assertFalse(raised.exception.retryable)

    def test_gemini_request_converts_roles_and_extracts_text(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "candidates": [{
                "content": {"parts": [{"text": '{"ok":true}'}]},
            }],
        }
        messages = [
            {"role": "system", "content": "Follow policy"},
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "prior answer"},
        ]
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch("app.llm.clients.requests.post", return_value=response) as post,
        ):
            result = clients._call_gemini(
                messages,
                model="gemini-test",
                response_format={"type": "json_object"},
            )

        self.assertEqual(result.content, '{"ok":true}')
        self.assertEqual(result.provider, "gemini")
        self.assertEqual(post.call_args.args[0], "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["system_instruction"]["parts"][0]["text"], "Follow policy")
        self.assertEqual(payload["contents"][0]["role"], "user")
        self.assertEqual(payload["contents"][1]["role"], "model")
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")

    def test_gemini_non_success_response_raises_provider_error(self):
        response = Mock(status_code=503, text="unavailable")
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch("app.llm.clients.requests.post", return_value=response),
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                clients._call_gemini([{"role": "user", "content": "question"}])

    def test_groq_sdk_receives_chat_request_and_returns_content(self):
        completions = Mock()
        completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))]
        )
        fake_groq_module = ModuleType("groq")
        fake_groq_module.Groq = Mock(return_value=SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        ))
        with (
            patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}),
            patch.dict(sys.modules, {"groq": fake_groq_module}),
        ):
            result = clients._call_groq(
                [{"role": "user", "content": "question"}],
                model="openai/gpt-oss-120b",
                no_fallbacks=True,
            )

        self.assertEqual(result.content, "answer")
        self.assertEqual(result.provider, "groq")
        self.assertEqual(result.model, "openai/gpt-oss-120b")
        call_args = completions.create.call_args.kwargs
        self.assertEqual(call_args["model"], "openai/gpt-oss-120b")
        self.assertEqual(call_args["messages"], [{"role": "user", "content": "question"}])
        self.assertNotIn("allow_fallback", call_args)
        self.assertNotIn("no_fallbacks", call_args)

    def test_provider_keys_are_required_without_making_requests(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                clients._call_openrouter([])
            with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
                clients._call_gemini([])
            with self.assertRaisesRegex(ValueError, "GROQ_API_KEY"):
                clients._call_groq([])


if __name__ == "__main__":
    unittest.main()
