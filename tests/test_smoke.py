"""The smoke check: boot, readiness, routes, commands and teardown."""

import socket
import tempfile
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_checks import common, smoke  # noqa: E402


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class SmokeRun:
    """Runs smoke.main() with the given inputs and collects what it wrote."""

    def __init__(self, **inputs):
        self.workspace = tempfile.mkdtemp()
        self.outputs = os.path.join(self.workspace, "outputs")
        self.summary = os.path.join(self.workspace, "summary")
        self.artifacts = os.path.join(self.workspace, "artifacts")
        env = {
            "QA_WORKSPACE": self.workspace,
            "QA_ARTIFACTS_DIR": self.artifacts,
            "GITHUB_OUTPUT": self.outputs,
            "GITHUB_STEP_SUMMARY": self.summary,
        }
        env.update({"QA_SMOKE_%s" % key.upper(): value for key, value in inputs.items()})
        with mock.patch.dict(os.environ, env, clear=False):
            self.exit_code = smoke.main()

    def output(self, name):
        with open(self.outputs, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
        return None

    def summary_text(self):
        with open(self.summary, encoding="utf-8") as handle:
            return handle.read()


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.url = "http://127.0.0.1:%d/" % self.port
        self.serve = "%s -m http.server %d --bind 127.0.0.1" % (sys.executable, self.port)

    def test_nothing_configured_is_skipped_and_green(self):
        run = SmokeRun()
        self.assertEqual((run.exit_code, run.output("status")), (0, "SKIPPED"))
        self.assertIn("Skipping smoke", run.summary_text())

    def test_routes_and_commands_pass(self):
        run = SmokeRun(
            start_command=self.serve,
            ready_url=self.url,
            routes="/\n/missing=404",
            commands="echo hello\ntrue",
            setup_command="echo setup > setup.txt",
        )
        self.assertEqual((run.exit_code, run.output("status")), (0, "PASS"))
        self.assertEqual(run.output("ready_url"), self.url)
        self.assertTrue(os.path.exists(os.path.join(run.workspace, "setup.txt")))
        self.assertTrue(os.path.exists(os.path.join(run.artifacts, "smoke-server.log")))
        self.assertIn("| route /missing | pass |", run.summary_text())

    def test_a_wrong_status_fails_the_run(self):
        run = SmokeRun(start_command=self.serve, ready_url=self.url, routes="/missing")
        self.assertEqual((run.exit_code, run.output("status")), (1, "FAIL"))
        self.assertIn("expected 200", run.summary_text())

    def test_fail_on_none_keeps_the_job_green(self):
        run = SmokeRun(
            start_command=self.serve, ready_url=self.url, routes="/missing", fail_on="none"
        )
        self.assertEqual((run.exit_code, run.output("status")), (0, "FAIL"))

    def test_a_failing_command_fails_the_run(self):
        run = SmokeRun(commands="true\nfalse")
        self.assertEqual((run.exit_code, run.output("status")), (1, "FAIL"))
        self.assertIn("`false` exit 1", run.summary_text())

    def test_a_server_that_exits_is_reported_with_its_log(self):
        run = SmokeRun(start_command="echo boom; exit 3", ready_url=self.url, ready_timeout="10")
        self.assertEqual(run.output("status"), "FAIL")
        self.assertIn("exited with code 3", run.summary_text())
        with open(os.path.join(run.artifacts, "smoke-server.log")) as handle:
            self.assertIn("boom", handle.read())

    def test_a_server_that_never_answers_times_out(self):
        run = SmokeRun(start_command="sleep 30", ready_url=self.url, ready_timeout="3")
        self.assertEqual(run.output("status"), "FAIL")
        self.assertIn("did not answer within 3s", run.summary_text())

    def test_a_failed_setup_stops_before_starting(self):
        run = SmokeRun(
            setup_command="exit 7", start_command=self.serve, ready_url=self.url, routes="/"
        )
        self.assertEqual(run.output("status"), "FAIL")
        self.assertNotIn("| route / |", run.summary_text())

    def test_hanging_commands_are_killed(self):
        run = SmokeRun(commands="sleep 30", command_timeout="1")
        self.assertEqual(run.output("status"), "FAIL")
        self.assertIn("timed out after 1s", run.summary_text())

    def test_the_server_is_stopped_afterwards(self):
        pidfile = os.path.join(tempfile.mkdtemp(), "pid")
        SmokeRun(
            start_command="echo $$ > %s; exec %s" % (pidfile, self.serve),
            ready_url=self.url,
            routes="/",
        )
        with open(pidfile) as handle:
            pid = int(handle.read())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class SmokeConfigTests(unittest.TestCase):
    def test_start_and_compose_are_exclusive(self):
        env = {"QA_SMOKE_START_COMMAND": "x", "QA_SMOKE_COMPOSE_FILE": "y"}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(common.ConfigError):
                smoke.from_env()

    def test_routes_need_a_ready_url(self):
        with mock.patch.dict(os.environ, {"QA_SMOKE_ROUTES": "/"}, clear=False):
            with self.assertRaises(common.ConfigError):
                smoke.from_env()

    def test_route_specs(self):
        config = mock.Mock(ready_url="http://h:1/base/")
        with mock.patch.object(smoke.server, "status", return_value=302) as status:
            name, ok, detail = smoke._route(config, "/login=302")
            self.assertTrue(ok)
            status.assert_called_with("http://h:1/login")
            self.assertFalse(smoke._route(config, "http://other/x")[1])
            status.assert_called_with("http://other/x")


if __name__ == "__main__":
    unittest.main()
