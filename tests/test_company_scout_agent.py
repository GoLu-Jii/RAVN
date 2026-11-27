import sys
import types
import unittest
from unittest.mock import patch

from app.intelligence_desk.agents import company_scout


class CompanyScoutAgentModelTests(unittest.TestCase):
    def test_uses_openrouter_with_three_model_fallbacks(self):
        created = {}

        class FakeChatOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        openai_module = types.ModuleType("langchain_openai")
        openai_module.ChatOpenAI = FakeChatOpenAI

        def fake_create_agent(**kwargs):
            created.update(kwargs)
            return "agent"

        with patch.dict(sys.modules, {"langchain_openai": openai_module}), \
                patch.dict("os.environ", {"OPENROUTER_API_KEY": "openrouter-test"}, clear=True), \
                patch.object(company_scout, "create_agent", side_effect=fake_create_agent), \
                patch.object(company_scout, "build_company_scout_tools", return_value=[]):
            agent = company_scout.create_company_scout_agent(1)

        self.assertEqual(agent, "agent")
        model = created["model"]
        self.assertEqual(model.kwargs["api_key"], "openrouter-test")
        self.assertEqual(model.kwargs["base_url"], company_scout.OPENROUTER_BASE_URL)
        self.assertEqual(model.kwargs["model"], company_scout.COMPANY_SCOUT_MODELS[0])
        self.assertEqual(
            model.kwargs["extra_body"]["models"],
            list(company_scout.COMPANY_SCOUT_MODELS),
        )
        self.assertEqual(len(model.kwargs["extra_body"]["models"]), 3)
        self.assertNotIn("middleware", created)

    def test_requires_openrouter_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                company_scout.create_company_scout_agent(1)


if __name__ == "__main__":
    unittest.main()
