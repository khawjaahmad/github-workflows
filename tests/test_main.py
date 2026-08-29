"""The entry point: exit codes, and never losing the report."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_agent import __main__ as entry, config as config_module, github  # noqa: E402

REPORT = "## QA Report\n\n**Status: %s**"


class MainTest(unittest.TestCase):
    def setUp(self):
        self.original = dict(os.environ)
        self.addCleanup(self._restore)
        for name in [n for n in os.environ if n.startswith(("QA_", "GITHUB_"))]:
            del os.environ[name]

        self.temp = tempfile.mkdtemp()
        os.environ.update(
            {
                "QA_API_KEY": "k",
                "QA_MODEL": "stub-model",
                "QA_REPO": "acme/widget",
                "QA_PR_NUMBER": "7",
                "QA_WORKSPACE": self.temp,
                "QA_POST_COMMENT": "false",
                "GITHUB_OUTPUT": os.path.join(self.temp, "output"),
                "GITHUB_STEP_SUMMARY": os.path.join(self.temp, "summary"),
            }
        )

        self.pull_request = mock.patch.object(github, "get_pull_request", return_value={})
        self.diff = mock.patch.object(github, "get_diff", return_value="diff")
        self.pull_request.start()
        self.diff.start()
        self.addCleanup(self.pull_request.stop)
        self.addCleanup(self.diff.stop)

    def _restore(self):
        os.environ.clear()
        os.environ.update(self.original)

    def run_with(self, status):
        with mock.patch.object(entry.agent, "run", return_value=(status, REPORT % status)):
            return entry.main()

    def read(self, name):
        with open(os.path.join(self.temp, name)) as handle:
            return handle.read()

    def test_pass_is_green(self):
        self.assertEqual(self.run_with("PASS"), 0)

    def test_fail_turns_the_check_red(self):
        self.assertEqual(self.run_with("FAIL"), 1)

    def test_partial_does_not_turn_the_check_red_by_default(self):
        self.assertEqual(self.run_with("PARTIAL"), 0)

    def test_fail_on_none_keeps_the_job_green(self):
        os.environ["QA_FAIL_ON"] = "none"
        self.assertEqual(self.run_with("FAIL"), 0)

    def test_fail_on_partial_is_configurable(self):
        os.environ["QA_FAIL_ON"] = "FAIL,PARTIAL"
        self.assertEqual(self.run_with("PARTIAL"), 1)

    def test_the_report_reaches_the_summary_and_the_output(self):
        self.run_with("PASS")
        self.assertIn("**Status: PASS**", self.read("summary"))
        self.assertEqual(self.read("output").strip(), "status=PASS")

    def test_the_report_is_posted_when_asked(self):
        os.environ["QA_POST_COMMENT"] = "true"
        with mock.patch.object(github, "upsert_report") as upsert:
            self.run_with("PASS")
        self.assertIn("**Status: PASS**", upsert.call_args[0][3])

    def test_a_posting_failure_does_not_discard_the_report(self):
        os.environ["QA_POST_COMMENT"] = "true"
        with mock.patch.object(
            github, "upsert_report", side_effect=github.GitHubError("403 forbidden")
        ):
            code = self.run_with("PASS")

        # The verdict still stands, and the report is still readable.
        self.assertEqual(code, 0)
        self.assertIn("**Status: PASS**", self.read("summary"))

    def test_a_bad_configuration_exits_two(self):
        os.environ["QA_PR_NUMBER"] = "not-a-number"
        self.assertEqual(entry.main(), 2)

    def test_an_unreadable_pull_request_exits_two(self):
        with mock.patch.object(
            github, "get_pull_request", side_effect=github.GitHubError("404")
        ):
            self.assertEqual(entry.main(), 2)

    def test_the_artifacts_directory_is_created_for_the_agent(self):
        artifacts = os.path.join(self.temp, "artifacts")
        os.environ["QA_ARTIFACTS_DIR"] = artifacts
        self.run_with("PASS")
        self.assertTrue(os.path.isdir(artifacts))


class DiffBudgetTest(unittest.TestCase):
    def config(self, context_tokens):
        return config_module.Config(
            api_key="k", base_url="u", model="m", provider="zai", github_token="t",
            repo="a/b", pr_number=1, workspace=".", max_turns=1, command_timeout=1,
            post_comment=False, setup_command="", effort="",
            context_tokens=context_tokens,
        )

    def test_the_diff_never_takes_the_whole_context(self):
        # Unknown window: a flat cap, as before.
        self.assertEqual(entry._diff_budget(self.config(0)), 120000)
        # A small window keeps the diff to a floor it can still work with.
        self.assertEqual(entry._diff_budget(self.config(20000)), 20000)
        # A large one is capped, so the diff never crowds out the run.
        self.assertEqual(entry._diff_budget(self.config(1000000)), 120000)


if __name__ == "__main__":
    unittest.main()
