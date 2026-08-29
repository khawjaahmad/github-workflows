"""The client's request budgeting and its split between what is published and
what is only logged."""

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import LLMServer, make_config, report_turn  # noqa: E402

from qa_agent import llm  # noqa: E402


class AttemptTimeoutTest(unittest.TestCase):
    """One slow endpoint must not outlive the budget the report is written in."""

    def setUp(self):
        self.config = make_config(request_timeout=600)

    def test_without_a_deadline_the_configured_timeout_applies(self):
        self.assertEqual(llm._attempt_timeout(self.config, None), 600)

    def test_plenty_of_budget_left_uses_the_configured_timeout(self):
        with mock.patch("qa_agent.llm.time.monotonic", return_value=1000.0):
            self.assertEqual(llm._attempt_timeout(self.config, 3000.0), 600)

    def test_a_request_cannot_run_past_the_budget(self):
        with mock.patch("qa_agent.llm.time.monotonic", return_value=1000.0):
            self.assertEqual(llm._attempt_timeout(self.config, 1120.0), 120)

    def test_a_wind_down_turn_still_gets_time_to_ask_for_the_report(self):
        with mock.patch("qa_agent.llm.time.monotonic", return_value=1000.0):
            self.assertEqual(llm._attempt_timeout(self.config, 900.0), llm.WIND_DOWN_TIMEOUT)

    def test_spent(self):
        self.assertFalse(llm._spent(None))
        with mock.patch("qa_agent.llm.time.monotonic", return_value=1000.0):
            self.assertTrue(llm._spent(999.0))
            self.assertFalse(llm._spent(1001.0))


class BackoffTest(unittest.TestCase):
    def test_the_servers_own_advice_wins(self):
        self.assertEqual(llm._backoff(3, "3"), 3.0)
        # "wait none" is a valid instruction, not a missing header.
        self.assertEqual(llm._backoff(3, "0"), 0.0)

    def test_a_nonsense_header_falls_back_to_jittered_backoff(self):
        self.assertGreaterEqual(llm._backoff(0, "soon"), 1.0)
        self.assertLessEqual(llm._backoff(0, "soon"), 2.0)

    def test_backoff_is_capped(self):
        self.assertLessEqual(llm._backoff(20, None), llm.MAX_BACKOFF)
        self.assertEqual(llm._backoff(0, "9999"), llm.MAX_BACKOFF)


class DisclosureTest(unittest.TestCase):
    """The report is a public comment; the log is not."""

    def test_a_structured_message_is_publishable_and_the_body_is_not(self):
        detail = '{"error": {"code": "1310", "message": "Weekly Limit Exhausted", "trace": "x"}}'
        error = llm._http_error(make_config(), "https://gw.internal/v1", 429, detail)

        self.assertIn("Weekly Limit Exhausted", str(error))
        self.assertIn("quota or subscription limit", str(error))
        self.assertNotIn("gw.internal", str(error))
        self.assertNotIn("trace", str(error))
        self.assertIn("gw.internal", error.detail)

    def test_an_unstructured_body_is_not_published_at_all(self):
        error = llm._http_error(make_config(), "https://gw.internal/v1", 502,
                                "<html>proxy.corp says no</html>")

        self.assertNotIn("proxy.corp", str(error))
        self.assertIn("HTTP 502", str(error))
        self.assertIn("proxy.corp", error.detail)

    def test_provider_message_extraction(self):
        self.assertEqual(llm._provider_message('{"error": {"message": " hi "}}'), "hi")
        self.assertEqual(llm._provider_message("<html>nope</html>"), "")
        self.assertEqual(llm._provider_message('{"error": {"message": 5}}'), "")


class RetryDeadlineTest(unittest.TestCase):
    """Five attempts at the default 600s timeout is fifty minutes; the job is
    thirty. Retries have to answer to the run's budget."""

    def serve(self, turns):
        server = LLMServer(turns).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        patcher = mock.patch("qa_agent.llm.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        return server, make_config(base_url=server.url + "/v1", request_timeout=5)

    def test_retries_continue_while_there_is_budget(self):
        server, config = self.serve([500] * 5)

        with self.assertRaises(llm.LLMError):
            llm.complete(config, [], [], deadline=time.monotonic() + 3600)

        self.assertEqual(len(server.requests), 5)

    def test_retries_stop_once_the_budget_is_spent(self):
        server, config = self.serve([500] * 5)

        with self.assertRaises(llm.LLMError):
            llm.complete(config, [], [], deadline=time.monotonic() - 1)

        # One attempt, not five: the wind-down still needs the time that is left.
        self.assertEqual(len(server.requests), 1)

    def test_a_spent_budget_does_not_stop_a_request_that_works(self):
        server, config = self.serve([report_turn("PASS")])

        completion = llm.complete(config, [], [], deadline=time.monotonic() - 1)

        self.assertTrue(completion.message["tool_calls"])
        self.assertEqual(len(server.requests), 1)


class UsageTest(unittest.TestCase):
    def test_nonsense_token_counts_do_not_crash_the_run(self):
        self.assertEqual(llm._count("120"), 120)
        self.assertEqual(llm._count(None), 0)
        self.assertEqual(llm._count("lots"), 0)
        self.assertEqual(llm._count(-5), 0)


if __name__ == "__main__":
    unittest.main()
