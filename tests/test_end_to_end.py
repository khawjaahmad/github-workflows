"""The whole action, from environment variables to a posted comment."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import GitHubServer, LLMServer, bash_turn, report_turn, tool_call  # noqa: E402

from qa_agent import __main__ as entry  # noqa: E402


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.original = dict(os.environ)
        self.addCleanup(self._restore)
        for name in [n for n in os.environ if n.startswith(("QA_", "GITHUB_"))]:
            del os.environ[name]

        self.workspace = tempfile.mkdtemp()
        self.temp = tempfile.mkdtemp()
        with open(os.path.join(self.workspace, "go.mod"), "w") as handle:
            handle.write("module example.com/widget\n")

    def _restore(self):
        os.environ.clear()
        os.environ.update(self.original)

    def serve(self, turns, comments=()):
        llm = LLMServer(turns).start()
        api = GitHubServer(comments).start()
        for server in (llm, api):
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        os.environ.update(
            {
                "QA_API_KEY": "provider-secret",
                "QA_PROVIDER": "custom",
                "QA_BASE_URL": llm.url + "/v1",
                "QA_MODEL": "stub-model",
                "QA_GITHUB_API_URL": api.url,
                "QA_GITHUB_TOKEN": "gh-secret",
                "QA_REPO": "acme/widget",
                "QA_PR_NUMBER": "7",
                "QA_WORKSPACE": self.workspace,
                "QA_ARTIFACTS_DIR": os.path.join(self.temp, "artifacts"),
                "QA_RUN_URL": "https://example/run/1",
                "QA_COMMAND_TIMEOUT": "30",
                "GITHUB_OUTPUT": os.path.join(self.temp, "output"),
                "GITHUB_STEP_SUMMARY": os.path.join(self.temp, "summary"),
            }
        )
        return llm, api

    def read(self, name):
        with open(os.path.join(self.temp, name)) as handle:
            return handle.read()

    def test_a_passing_run_posts_a_report(self):
        llm, api = self.serve(
            [
                bash_turn("go version && cat go.mod"),
                {
                    "content": "Endpoint responds.",
                    "tool_calls": [
                        tool_call(
                            "submit_report",
                            {
                                "status": "PASS",
                                "changes_tested": "- the /api/health endpoint",
                                "evidence": "1. `curl localhost:8080/health` -> 200 OK",
                                "edge_cases": "- empty database",
                            },
                            "call_2",
                        )
                    ],
                },
            ]
        )

        code = entry.main()

        self.assertEqual(code, 0)
        self.assertEqual(len(api.comments), 1)
        posted = api.comments[0]["body"]
        self.assertIn("QA of commit ", posted)
        self.assertIn("**Status: PASS**", posted)
        self.assertIn("curl localhost:8080/health", posted)
        self.assertEqual(self.read("output").strip(), "status=PASS")

        self.assertIn("go.mod (Go)", llm.requests[0]["messages"][1]["content"])
        result = [m for m in llm.requests[1]["messages"] if m["role"] == "tool"][0]
        self.assertIn("module example.com/widget", result["content"])

    def test_a_failing_verdict_turns_the_check_red_and_still_reports(self):
        _, api = self.serve([report_turn("FAIL")])

        self.assertEqual(entry.main(), 1)
        self.assertIn("**Status: FAIL**", api.comments[0]["body"])

    def test_a_rerun_leaves_the_previous_report_in_place(self):
        _, api = self.serve(
            [report_turn("PASS")],
            comments=[
                {"id": 5, "body": "a human comment"},
                {"id": 6, "body": "## QA Report\n\n**Status: FAIL**"},
            ],
        )

        entry.main()

        self.assertEqual(len(api.comments), 3)
        self.assertIn("**Status: FAIL**", api.comments[1]["body"])
        self.assertIn("**Status: PASS**", api.comments[2]["body"])

    def test_the_agents_commands_cannot_read_the_secrets(self):
        llm, api = self.serve(
            [bash_turn("echo key=[$QA_API_KEY] token=[$QA_GITHUB_TOKEN]"), report_turn("PASS")]
        )

        entry.main()

        result = [m for m in llm.requests[1]["messages"] if m["role"] == "tool"][0]
        self.assertIn("key=[] token=[]", result["content"])
        self.assertNotIn("provider-secret", result["content"])
        self.assertNotIn("gh-secret", result["content"])

    def test_a_file_the_agent_saves_is_listed_in_the_report(self):
        artifacts = os.path.join(self.temp, "artifacts")
        _, api = self.serve(
            [bash_turn('echo shot > "$QA_ARTIFACTS_DIR/dashboard.png"'), report_turn("PASS")]
        )

        entry.main()

        self.assertTrue(os.path.isfile(os.path.join(artifacts, "dashboard.png")))
        self.assertIn("dashboard.png", api.comments[0]["body"])
        self.assertIn("https://example/run/1", api.comments[0]["body"])

    def test_a_dead_provider_still_produces_a_report(self):
        _, api = self.serve([500, 500, 500, 500, 500])

        with mock.patch("qa_agent.llm.time.sleep"):
            code = entry.main()

        self.assertEqual(code, 0)
        self.assertIn("**Status: PARTIAL**", api.comments[0]["body"])
        self.assertIn("model endpoint failed", api.comments[0]["body"])


if __name__ == "__main__":
    unittest.main()
