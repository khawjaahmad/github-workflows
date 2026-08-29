"""Resolving the action's inputs."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_agent import config as config_module  # noqa: E402

REQUIRED = {"QA_API_KEY": "k", "QA_REPO": "acme/widget", "QA_PR_NUMBER": "7"}


class FromEnvTest(unittest.TestCase):
    def setUp(self):
        self.original = dict(os.environ)
        self.addCleanup(self._restore)
        for name in [n for n in os.environ if n.startswith("QA_")]:
            del os.environ[name]
        os.environ.update(REQUIRED)

    def _restore(self):
        os.environ.clear()
        os.environ.update(self.original)

    def test_provider_defaults(self):
        config = config_module.from_env()
        self.assertEqual(config.base_url, "https://api.z.ai/api/coding/paas/v4")
        self.assertEqual(config.model, "glm-5.3")

    def test_model_overrides_the_provider_default(self):
        os.environ.update({"QA_PROVIDER": "gemini", "QA_MODEL": "gemini-2.5-flash"})
        config = config_module.from_env()
        self.assertEqual(config.base_url, "https://generativelanguage.googleapis.com/v1beta/openai")
        self.assertEqual(config.model, "gemini-2.5-flash")

    def test_custom_provider_needs_a_base_url(self):
        os.environ["QA_PROVIDER"] = "custom"
        with self.assertRaises(config_module.ConfigError):
            config_module.from_env()

        os.environ.update({"QA_BASE_URL": "https://gateway.internal/v1/", "QA_MODEL": "mine"})
        self.assertEqual(config_module.from_env().base_url, "https://gateway.internal/v1")

    def test_unknown_provider_is_rejected(self):
        os.environ["QA_PROVIDER"] = "anthropic"
        with self.assertRaises(config_module.ConfigError) as raised:
            config_module.from_env()
        self.assertIn("custom", str(raised.exception))

    def test_missing_key_and_pr_number_are_explained(self):
        os.environ["QA_API_KEY"] = ""
        with self.assertRaises(config_module.ConfigError) as raised:
            config_module.from_env()
        self.assertIn("LLM_API_KEY", str(raised.exception))

        os.environ.update(REQUIRED)
        os.environ["QA_PR_NUMBER"] = ""
        with self.assertRaises(config_module.ConfigError) as raised:
            config_module.from_env()
        self.assertIn("pr_number", str(raised.exception))

    def test_numeric_inputs_are_validated(self):
        os.environ["QA_MAX_TURNS"] = "soon"
        with self.assertRaises(config_module.ConfigError) as raised:
            config_module.from_env()
        self.assertIn("QA_MAX_TURNS", str(raised.exception))

    def test_budget_defaults(self):
        config = config_module.from_env()
        self.assertEqual(config.max_turns, 40)
        self.assertEqual(config.command_timeout, 300)
        self.assertEqual(config.request_timeout, 600)
        self.assertEqual(config.time_budget, 1500)
        self.assertEqual(config.max_context_chars, 240000)
        self.assertEqual(config.fail_on, ("FAIL",))
        self.assertTrue(config.post_comment)


class FailOnTest(unittest.TestCase):
    def test_default_and_lists(self):
        self.assertEqual(config_module.parse_fail_on("FAIL"), ("FAIL",))
        self.assertEqual(config_module.parse_fail_on("fail, partial"), ("FAIL", "PARTIAL"))

    def test_advisory_mode(self):
        # How the action is rolled out before it is trusted to gate merges.
        self.assertEqual(config_module.parse_fail_on("none"), ())
        self.assertEqual(config_module.parse_fail_on(""), ())

    def test_nonsense_is_rejected(self):
        with self.assertRaises(config_module.ConfigError):
            config_module.parse_fail_on("maybe")


if __name__ == "__main__":
    unittest.main()
