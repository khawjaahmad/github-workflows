"""End-to-end test of the agent loop against a stubbed OpenAI-compatible server."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_agent import agent, config as config_module, github, llm, tools  # noqa: E402

PULL_REQUEST = {
    "number": 7,
    "title": "Add /api/health endpoint",
    "body": "Returns 200 with the version.",
    "base": {"ref": "main", "repo": {"full_name": "acme/widget"}},
    "head": {"ref": "feature"},
}


class StubHandler(BaseHTTPRequestHandler):
    """Replays scripted assistant turns and records what the agent sent."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        self._respond(200, {"choices": [{"message": self.server.turns.pop(0)}]})

    def _respond(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def tool_call(name, arguments, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), StubHandler)
        self.server.requests = []
        self.server.turns = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.workspace = tempfile.mkdtemp()

    def config(self, max_turns=10, effort=""):
        host, port = self.server.server_address
        return config_module.Config(
            api_key="test-key",
            base_url="http://%s:%d/v1" % (host, port),
            model="glm-5.3",
            provider="zai",
            github_token="token",
            repo="acme/widget",
            pr_number=7,
            workspace=self.workspace,
            max_turns=max_turns,
            command_timeout=30,
            post_comment=False,
            setup_command="",
            effort=effort,
        )

    def test_runs_commands_then_reports(self):
        self.server.turns = [
            {"content": "Exercising the endpoint.", "tool_calls": [tool_call("bash", {"command": "echo hello-from-qa"})]},
            {
                "content": None,
                "tool_calls": [
                    tool_call(
                        "submit_report",
                        {
                            "status": "PASS",
                            "changes_tested": "- /api/health",
                            "evidence": "1. `echo hello-from-qa` -> hello-from-qa",
                            "edge_cases": "- none",
                        },
                        call_id="call_2",
                    )
                ],
            },
        ]

        status, body = agent.run(self.config(), PULL_REQUEST, "diff --git a/app.py b/app.py")

        self.assertEqual(status, "PASS")
        self.assertIn("**Status: PASS**", body)
        self.assertIn("### Evidence", body)
        self.assertIn("hello-from-qa", body)

        # The second request must carry the real command output back to the model.
        second = self.server.requests[1]["messages"]
        tool_result = [m for m in second if m["role"] == "tool"][0]
        self.assertEqual(tool_result["tool_call_id"], "call_1")
        self.assertIn("hello-from-qa", tool_result["content"])
        self.assertIn("exit code: 0", tool_result["content"])

        # The PR context and the tool schema must reach the provider.
        first = self.server.requests[0]
        self.assertIn("Add /api/health endpoint", first["messages"][1]["content"])
        self.assertEqual(
            sorted(t["function"]["name"] for t in first["tools"]), ["bash", "submit_report"]
        )

    def test_effort_is_sent_verbatim_when_set(self):
        self.server.turns = [
            {
                "content": None,
                "tool_calls": [
                    tool_call("submit_report", {"status": "PASS", "changes_tested": "-", "evidence": "-"})
                ],
            }
        ]

        agent.run(self.config(effort="max"), PULL_REQUEST, "")

        # Passed through unvalidated — provider vocabularies differ (z.ai
        # low/high/max, OpenAI low/medium/high/xhigh).
        self.assertEqual(self.server.requests[0]["reasoning_effort"], "max")

    def test_effort_is_omitted_when_unset(self):
        self.server.turns = [
            {
                "content": None,
                "tool_calls": [
                    tool_call("submit_report", {"status": "PASS", "changes_tested": "-", "evidence": "-"})
                ],
            }
        ]

        agent.run(self.config(), PULL_REQUEST, "")

        self.assertNotIn("reasoning_effort", self.server.requests[0])

    def test_gives_up_after_max_turns(self):
        self.server.turns = [
            {"content": "thinking", "tool_calls": [tool_call("bash", {"command": "true"})]}
            for _ in range(2)
        ]

        status, body = agent.run(self.config(max_turns=2), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("ran out of turns", body)

    def test_bad_tool_arguments_are_reported_to_the_model(self):
        self.server.turns = [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{not json"},
                    }
                ],
            },
            {
                "content": None,
                "tool_calls": [
                    tool_call(
                        "submit_report",
                        {"status": "FAIL", "changes_tested": "-", "evidence": "-"},
                        call_id="call_2",
                    )
                ],
            },
        ]

        status, _ = agent.run(self.config(), PULL_REQUEST, "")

        self.assertEqual(status, "FAIL")
        tool_result = [m for m in self.server.requests[1]["messages"] if m["role"] == "tool"][0]
        self.assertIn("not valid JSON", tool_result["content"])


class UnitTest(unittest.TestCase):
    def test_provider_defaults(self):
        os.environ.update(
            {
                "QA_PROVIDER": "zai",
                "QA_API_KEY": "k",
                "QA_REPO": "acme/widget",
                "QA_PR_NUMBER": "7",
                "QA_BASE_URL": "",
                "QA_MODEL": "",
            }
        )
        config = config_module.from_env()
        self.assertEqual(config.base_url, "https://api.z.ai/api/coding/paas/v4")
        self.assertEqual(config.model, "glm-5.3")

        os.environ["QA_PROVIDER"] = "gemini"
        os.environ["QA_MODEL"] = "gemini-2.5-flash"
        config = config_module.from_env()
        self.assertEqual(config.base_url, "https://generativelanguage.googleapis.com/v1beta/openai")
        self.assertEqual(config.model, "gemini-2.5-flash")

        os.environ["QA_PROVIDER"] = "custom"
        os.environ["QA_BASE_URL"] = "https://gateway.internal/v1/"
        config = config_module.from_env()
        self.assertEqual(config.base_url, "https://gateway.internal/v1")

        os.environ["QA_PROVIDER"] = "anthropic"
        with self.assertRaises(config_module.ConfigError):
            config_module.from_env()

    def test_normalize_fills_missing_content(self):
        message = llm.normalize({"tool_calls": [{"function": {"name": "bash"}}]})
        self.assertEqual(message["content"], "")
        self.assertEqual(message["tool_calls"][0]["id"], "call_0")
        self.assertEqual(message["tool_calls"][0]["function"]["arguments"], "{}")

    def test_invalid_status_falls_back_to_partial(self):
        status, body = tools.render_report(
            {"status": "probably fine", "changes_tested": "- a", "evidence": "- b"}
        )
        self.assertEqual(status, "PARTIAL")
        self.assertNotIn("### Edge Cases", body)

    def test_backgrounded_process_does_not_block(self):
        # A dev server started with & keeps the child's stdout open; the tool must still
        # return as soon as the shell exits.
        started = time.monotonic()
        result = tools.run_bash("sleep 30 & echo server-started", tempfile.mkdtemp(), timeout=20)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("server-started", result)
        self.assertIn("exit code: 0", result)

    def test_bash_timeout_reports_partial_output(self):
        result = tools.run_bash("echo before-hang; sleep 5", tempfile.mkdtemp(), timeout=1)
        self.assertIn("timed out", result)
        self.assertIn("before-hang", result)

    def test_bash_timeout_is_reported(self):
        result = tools.run_bash("sleep 5", tempfile.mkdtemp(), timeout=1)
        self.assertIn("timed out", result)

    def test_report_marker_is_embedded(self):
        self.assertTrue(github.REPORT_MARKER.startswith("<!--"))


if __name__ == "__main__":
    unittest.main()
