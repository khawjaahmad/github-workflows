"""The agent loop, against a stubbed OpenAI-compatible server."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from support import (  # noqa: E402
    PULL_REQUEST,
    LLMServer,
    bash_turn,
    make_config,
    report_turn,
    tool_call,
)

from qa_agent import agent  # noqa: E402


class AgentTest(unittest.TestCase):
    def serve(self, turns):
        server = LLMServer(turns).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def config(self, server, **overrides):
        overrides.setdefault("workspace", tempfile.mkdtemp())
        return make_config(base_url=server.url + "/v1", **overrides)

    def test_runs_commands_then_reports(self):
        server = self.serve([bash_turn("echo hello-from-qa"), report_turn("PASS")])

        status, body = agent.run(self.config(server), PULL_REQUEST, "diff --git a/app.py b/app.py")

        self.assertEqual(status, "PASS")
        self.assertIn("**Status: PASS**", body)
        self.assertIn("### Evidence", body)

        # The real command output must come back to the model.
        result = [m for m in server.requests[1]["messages"] if m["role"] == "tool"][0]
        self.assertEqual(result["tool_call_id"], "call_1")
        self.assertIn("hello-from-qa", result["content"])
        self.assertIn("exit code: 0", result["content"])

        # PR context and both tools reach the provider.
        first = server.requests[0]
        self.assertIn("Add /api/health endpoint", first["messages"][1]["content"])
        self.assertEqual(
            sorted(t["function"]["name"] for t in first["tools"]), ["bash", "submit_report"]
        )

    def test_effort_is_sent_verbatim_when_set(self):
        server = self.serve([report_turn()])
        agent.run(self.config(server, effort="max"), PULL_REQUEST, "")
        # Passed through unvalidated — provider vocabularies differ.
        self.assertEqual(server.requests[0]["reasoning_effort"], "max")

    def test_effort_is_omitted_when_unset(self):
        server = self.serve([report_turn()])
        agent.run(self.config(server), PULL_REQUEST, "")
        self.assertNotIn("reasoning_effort", server.requests[0])

    def test_bad_tool_arguments_are_reported_to_the_model(self):
        broken = {
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "bash", "arguments": "{not json"}}
            ],
        }
        server = self.serve([broken, report_turn("FAIL")])

        status, _ = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "FAIL")
        result = [m for m in server.requests[1]["messages"] if m["role"] == "tool"][0]
        self.assertIn("not valid JSON", result["content"])

    def test_unknown_tool_is_reported_to_the_model(self):
        server = self.serve([
            {"content": None, "tool_calls": [tool_call("teleport", {}, "call_1")]},
            report_turn(),
        ])

        agent.run(self.config(server), PULL_REQUEST, "")

        result = [m for m in server.requests[1]["messages"] if m["role"] == "tool"][0]
        self.assertIn("unknown tool", result["content"])

    def test_a_turn_without_tool_calls_is_nudged(self):
        server = self.serve([{"content": "Looks fine to me.", "tool_calls": []}, report_turn()])

        agent.run(self.config(server), PULL_REQUEST, "")

        self.assertIn("call submit_report", server.requests[1]["messages"][-1]["content"])


class WindDownTest(AgentTest):
    """The agent must always come back with a report."""

    def test_turn_budget_triggers_a_final_call_and_the_model_reports(self):
        server = self.serve([bash_turn("true"), bash_turn("true"), report_turn("PARTIAL")])

        status, body = agent.run(self.config(server, max_turns=2), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("Stop testing now", server.last_messages[-1]["content"])
        self.assertIn("2-turn budget is spent", server.last_messages[-1]["content"])
        self.assertNotIn("stopped before submitting", body)

    def test_a_model_that_never_reports_still_produces_one(self):
        server = self.serve([bash_turn("echo probing-the-server")] * 8)

        status, body = agent.run(self.config(server, max_turns=2), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("stopped before submitting a report", body)
        # The fallback carries what it actually tried, not just "it gave up".
        self.assertIn("echo probing-the-server", body)

    def test_time_budget_ends_the_run(self):
        server = self.serve([bash_turn("sleep 1.2"), report_turn("PARTIAL")])

        status, _ = agent.run(self.config(server, time_budget=1), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("time budget is spent", server.last_messages[-1]["content"])

    def test_command_timeout_never_outlives_the_time_budget(self):
        config = make_config(time_budget=100)
        with mock.patch("qa_agent.agent.time.monotonic", return_value=1000.0):
            self.assertEqual(agent._timeout(600, config, 1012.0), 12)
            self.assertEqual(agent._timeout(5, config, 1012.0), 5)
            # Always leaves enough for a command to at least start.
            self.assertEqual(agent._timeout(600, config, 995.0), 10)
        self.assertEqual(agent._timeout(None, make_config(command_timeout=30), None), 30)


class EndpointFailureTest(AgentTest):
    """A provider that fails must not cost the reviewer their report."""

    def setUp(self):
        patcher = mock.patch("qa_agent.llm.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_persistent_server_errors_produce_a_partial_report(self):
        server = self.serve([bash_turn("echo setup-worked"), 500, 500, 500, 500, 500])

        status, body = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("model endpoint failed", body)
        self.assertIn("echo setup-worked", body)

    def test_a_rejected_request_is_not_retried_but_is_reported(self):
        server = self.serve([(400, {"error": {"message": "context length exceeded"}})])

        status, body = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("HTTP 400", body)
        self.assertIn("max_context_chars", body)
        self.assertEqual(len(server.requests), 1)


class ContextTest(AgentTest):
    def test_the_transcript_is_compacted_before_it_overflows(self):
        loud = "echo %s" % ("x" * 500)
        server = self.serve(
            [bash_turn(loud, "call_%d" % i) for i in range(1, 13)] + [report_turn()]
        )

        agent.run(
            self.config(server, max_turns=20, max_context_chars=6000), PULL_REQUEST, "y" * 2000
        )

        final = server.last_messages
        self.assertIn("elided to fit the context window", "".join(m["content"] for m in final))
        # Every tool call still has its reply, or the provider would reject the request.
        called = [c["id"] for m in final for c in m.get("tool_calls") or []]
        replied = [m["tool_call_id"] for m in final if m["role"] == "tool"]
        self.assertEqual(called, replied)


class ArtifactsTest(AgentTest):
    def test_saved_files_are_listed_in_the_report(self):
        artifacts = tempfile.mkdtemp()
        with open(os.path.join(artifacts, "dashboard.png"), "w") as handle:
            handle.write("png")
        server = self.serve([report_turn("PASS")])

        _, body = agent.run(
            self.config(server, artifacts_dir=artifacts, run_url="https://example/run/1"),
            PULL_REQUEST,
            "",
        )

        self.assertIn("### Artifacts", body)
        self.assertIn("dashboard.png", body)
        self.assertIn("https://example/run/1", body)

    def test_no_artifacts_section_when_nothing_was_saved(self):
        server = self.serve([report_turn("PASS")])
        _, body = agent.run(
            self.config(server, artifacts_dir=tempfile.mkdtemp()), PULL_REQUEST, ""
        )
        self.assertNotIn("### Artifacts", body)


if __name__ == "__main__":
    unittest.main()
