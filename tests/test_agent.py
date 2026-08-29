"""The agent loop, against a stubbed OpenAI-compatible server."""

import contextlib
import io
import os
import signal
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from support import (  # noqa: E402
    PULL_REQUEST,
    LLMServer,
    bash_turn,
    completion_turn,
    make_config,
    report_turn,
    tool_call,
)

from qa_agent import agent  # noqa: E402


class AgentTestCase(unittest.TestCase):
    """Helpers only — no tests, so subclasses do not re-run them."""

    def serve(self, turns):
        server = LLMServer(turns).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def config(self, server, **overrides):
        overrides.setdefault("workspace", tempfile.mkdtemp())
        return make_config(base_url=server.url + "/v1", **overrides)


class AgentTest(AgentTestCase):
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


class WindDownTest(AgentTestCase):
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


class EndpointFailureTest(AgentTestCase):
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

    def test_a_non_json_body_is_reported_rather_than_raised(self):
        # A proxy answering 200 with an HTML error page. json.JSONDecodeError is
        # a ValueError, so it matched no handler and left as a traceback: exit 1,
        # no comment, empty summary.
        page = "<html><body>502 Bad Gateway at internal.proxy.corp</body></html>"
        server = self.serve([page] * 5)

        logged = io.StringIO()
        with contextlib.redirect_stdout(logged):
            status, body = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("not JSON", body)
        # The report is a public pull request comment, so the body itself stays
        # out of it — a proxy page can name internal hosts — and goes to the log.
        self.assertNotIn("internal.proxy.corp", body)
        self.assertIn("internal.proxy.corp", logged.getvalue())

    def test_an_empty_body_is_reported_rather_than_raised(self):
        server = self.serve([""] * 5)

        logged = io.StringIO()
        with contextlib.redirect_stdout(logged):
            status, body = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("not JSON", body)
        self.assertIn("(empty body)", logged.getvalue())

    def test_an_error_body_is_summarised_in_the_report_and_logged_in_full(self):
        server = self.serve(
            [(429, {"error": {"code": "1310", "message": "Weekly Limit Exhausted",
                              "request_id": "req-internal-9f3c"}})]
        )

        logged = io.StringIO()
        with contextlib.redirect_stdout(logged):
            _, body = agent.run(self.config(server), PULL_REQUEST, "")

        # The provider writes `message` for a developer to read, so it is safe to
        # publish. The rest of the body is diagnostics, and stays in the log.
        self.assertIn("Weekly Limit Exhausted", body)
        self.assertNotIn("req-internal-9f3c", body)
        self.assertIn("req-internal-9f3c", logged.getvalue())

    def test_a_transient_bad_body_is_retried(self):
        server = self.serve(["<html>502</html>", report_turn("PASS")])

        status, _ = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PASS")
        self.assertEqual(len(server.requests), 2)

    def test_a_rejected_request_is_not_retried_but_is_reported(self):
        # 1261 is z.ai's "Prompt too long". https://docs.z.ai/api-reference/api-code
        server = self.serve([(400, {"error": {"code": "1261", "message": "Prompt too long"}})])

        status, body = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("HTTP 400", body)
        self.assertIn("context_tokens", body)
        self.assertEqual(len(server.requests), 1)

    def test_an_exhausted_quota_is_not_retried(self):
        # z.ai returns 429 for quota and plan problems as well as rate limits;
        # only the rate limits are worth retrying. Codes 1308-1321 are quota.
        server = self.serve(
            [(429, {"error": {"code": "1310", "message": "Weekly Limit Exhausted"}})]
        )

        status, body = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertEqual(len(server.requests), 1)
        self.assertIn("quota or subscription limit", body)

    def test_a_real_rate_limit_is_retried(self):
        server = self.serve(
            [(429, {"error": {"code": "1302", "message": "Rate limit reached"}}),
             report_turn("PASS")]
        )

        status, _ = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PASS")
        self.assertEqual(len(server.requests), 2)


class TerminationTest(AgentTestCase):
    """A killed run must still report — this is how PR #3 was lost."""

    def test_a_command_that_kills_the_agent_still_produces_a_report(self):
        # The agent tidying up strays with `pkill -f "python -m qa_agent"` matches
        # its own process. Verbatim from the run that exited 143 with no comment.
        server = self.serve(
            [
                bash_turn("echo setup-worked", "call_1"),
                bash_turn("kill -TERM %d" % os.getpid(), "call_2"),
                report_turn("PASS"),
            ]
        )

        status, body = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("stopped by SIGTERM", body)
        self.assertIn("echo setup-worked", body)

    def test_the_previous_handlers_are_restored(self):
        before = signal.getsignal(signal.SIGTERM)
        agent.run(self.config(self.serve([report_turn("PASS")])), PULL_REQUEST, "")
        self.assertIs(signal.getsignal(signal.SIGTERM), before)


class ProviderBehaviourTest(AgentTestCase):
    """Behaviours the provider documents, which the loop has to honour."""

    def test_reasoning_content_is_returned_to_the_provider_unmodified(self):
        # z.ai's Preserved Thinking is on by default for the coding-plan endpoint
        # and wants reasoning blocks back "full, unmodified, and correctly
        # ordered". https://docs.z.ai/guides/capabilities/thinking-mode
        thought = "The endpoint is new, so I should curl it before trusting the diff."
        server = self.serve(
            [
                {
                    "content": "Checking.",
                    "reasoning_content": thought,
                    "tool_calls": [tool_call("bash", {"command": "true"}, "call_1")],
                },
                report_turn("PASS"),
            ]
        )

        agent.run(self.config(server), PULL_REQUEST, "")

        echoed = [m for m in server.last_messages if m.get("reasoning_content")]
        self.assertEqual(len(echoed), 1)
        self.assertEqual(echoed[0]["reasoning_content"], thought)

    def test_a_context_overflow_is_recognised_and_compacted(self):
        # The overflow arrives as a 200, not an error.
        # https://docs.z.ai/api-reference/llm/chat-completion
        server = self.serve(
            [
                bash_turn("echo one", "call_1"),
                completion_turn({"content": ""}, finish_reason="model_context_window_exceeded"),
                report_turn("PASS"),
            ]
        )

        status, _ = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PASS")
        self.assertEqual(len(server.requests), 3)

    def test_a_context_overflow_that_compacting_cannot_fix_is_reported(self):
        server = self.serve(
            [completion_turn({"content": ""}, finish_reason="model_context_window_exceeded")] * 2
        )

        status, body = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("context window was exceeded", body)

    def test_a_truncated_response_is_named_rather_than_read_as_silence(self):
        server = self.serve(
            [
                completion_turn({"content": "I was about to run"}, finish_reason="length"),
                report_turn("PASS"),
            ]
        )

        agent.run(self.config(server), PULL_REQUEST, "")

        self.assertIn("cut off at the output limit", server.last_messages[-1]["content"])

    def test_a_truncated_turn_that_still_called_a_tool_is_run_normally(self):
        # Appending a user message after an assistant turn with unanswered
        # tool_call ids makes the next request invalid.
        truncated = {
            "content": "Running it",
            "tool_calls": [tool_call("bash", {"command": "echo still-ran"}, "call_1")],
        }
        server = self.serve(
            [completion_turn(truncated, finish_reason="length"), report_turn("PASS")]
        )

        status, _ = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PASS")
        final = server.last_messages
        result = [m for m in final if m["role"] == "tool"][0]
        self.assertIn("still-ran", result["content"])
        called = [c["id"] for m in final for c in m.get("tool_calls") or []]
        replied = [m["tool_call_id"] for m in final if m["role"] == "tool"]
        self.assertEqual(called, replied)

    def test_a_blocked_response_ends_the_run_with_a_report(self):
        server = self.serve([completion_turn({"content": ""}, finish_reason="sensitive")])

        status, body = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("finish_reason: sensitive", body)

    def test_reported_token_usage_drives_compaction(self):
        # Under the ceiling: nothing is touched, however long the transcript is.
        loud = {
            "content": "x" * 4000,
            "tool_calls": [tool_call("bash", {"command": "echo hi"}, "call_1")],
        }
        server = self.serve(
            [
                completion_turn(loud, usage={"prompt_tokens": 10}),
                report_turn("PASS"),
            ]
        )
        agent.run(self.config(server, context_tokens=100000), PULL_REQUEST, "")
        self.assertNotIn("elided", "".join(m.get("content") or "" for m in server.last_messages))

    def test_compaction_starts_once_usage_passes_the_headroom(self):
        loud = {
            "content": "x" * 4000,
            "tool_calls": [tool_call("bash", {"command": "echo hi"}, "call_1")],
        }
        server = self.serve(
            [completion_turn(loud, usage={"prompt_tokens": 9000}) for _ in range(6)]
            + [report_turn("PASS")]
        )

        agent.run(self.config(server, max_turns=20, context_tokens=10000), PULL_REQUEST, "")

        self.assertIn("elided", "".join(m.get("content") or "" for m in server.last_messages))


class UnexpectedFailureTest(AgentTestCase):
    """Anything the loop does not anticipate must still leave a report."""

    def test_an_unexpected_exception_still_produces_a_report(self):
        server = self.serve([bash_turn("echo got-this-far"), report_turn("PASS")])

        logged = io.StringIO()
        with mock.patch.object(agent.tools, "run_bash", side_effect=OSError("no bash")):
            with contextlib.redirect_stdout(logged):
                status, body = agent.run(self.config(server), PULL_REQUEST, "")

        self.assertEqual(status, "PARTIAL")
        self.assertIn("unexpected failure", body)
        self.assertIn("OSError", body)
        # Loud where an engineer will look, graceful where the author will.
        self.assertIn("Traceback", logged.getvalue())

    def test_the_net_does_not_swallow_a_termination_signal(self):
        server = self.serve([bash_turn("kill -TERM %d" % os.getpid()), report_turn("PASS")])

        _, body = agent.run(self.config(server), PULL_REQUEST, "")

        # Interrupted is an Exception too: the specific handler has to win.
        self.assertIn("stopped by SIGTERM", body)
        self.assertNotIn("unexpected failure", body)


class CalibrationTest(unittest.TestCase):
    def test_the_ratio_is_measured_not_assumed(self):
        self.assertEqual(agent._calibrate(40000, 10000), 4.0)

    def test_a_nonsense_reading_cannot_make_the_budget_absurd(self):
        self.assertEqual(agent._calibrate(1000000, 10), 8.0)
        self.assertEqual(agent._calibrate(10, 1000), 1.5)
        self.assertEqual(agent._calibrate(0, 0), agent.CHARS_PER_TOKEN)

    def test_the_budget_uses_the_measured_ratio(self):
        config = make_config(context_tokens=10000)
        # 0.8 * 10000 tokens, at a measured 4 chars per token.
        self.assertEqual(agent._budget(config, 9000, 4.0), 32000)
        # Comfortably under the ceiling: nothing to do.
        self.assertEqual(agent._budget(config, 100, 4.0), 0)
        # No window stated: the agent does not guess one.
        self.assertEqual(agent._budget(make_config(context_tokens=0), 99999, 4.0), 0)


class ContextTest(AgentTestCase):
    def test_the_transcript_is_compacted_before_it_overflows(self):
        loud = "echo %s" % ("x" * 500)
        server = self.serve(
            [bash_turn(loud, "call_%d" % i) for i in range(1, 13)] + [report_turn()]
        )

        agent.run(
            self.config(server, max_turns=20, context_tokens=2000), PULL_REQUEST, "y" * 2000
        )

        final = server.last_messages
        self.assertIn("elided to fit the context window", "".join(m["content"] for m in final))
        # Every tool call still has its reply, or the provider would reject the request.
        called = [c["id"] for m in final for c in m.get("tool_calls") or []]
        replied = [m["tool_call_id"] for m in final if m["role"] == "tool"]
        self.assertEqual(called, replied)


class ArtifactsTest(AgentTestCase):
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
