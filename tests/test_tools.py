"""The bash tool, the report renderer, and the artifact listing."""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_agent import tools  # noqa: E402


class ChildEnvironmentTest(unittest.TestCase):
    """Nothing the action introduces may reach the commands the agent runs."""

    def test_our_secrets_are_removed(self):
        safe = tools.child_environment(
            {
                "QA_API_KEY": "provider-secret",
                "QA_GITHUB_TOKEN": "gh-secret",
                "LLM_API_KEY": "provider-secret",
                "GITHUB_TOKEN": "gh-secret",
                "ACTIONS_RUNTIME_TOKEN": "runtime-secret",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-secret",
                "INPUT_API_KEY": "provider-secret",
            }
        )
        self.assertEqual(safe, {})

    def test_the_runner_env_files_are_removed(self):
        safe = tools.child_environment(
            {
                "GITHUB_ENV": "/runner/env",
                "GITHUB_PATH": "/runner/path",
                "GITHUB_OUTPUT": "/runner/output",
                "GITHUB_STEP_SUMMARY": "/runner/summary",
            }
        )
        self.assertEqual(safe, {})

    def test_a_command_cannot_write_the_runner_env_file(self):
        os.environ["GITHUB_ENV"] = "/runner/env"
        self.addCleanup(os.environ.pop, "GITHUB_ENV", None)

        result = tools.run_bash("echo [$GITHUB_ENV]", tempfile.mkdtemp(), timeout=20)

        self.assertIn("[]", result)
        self.assertNotIn("/runner/env", result)

    def test_the_repository_environment_is_left_alone(self):
        safe = tools.child_environment(
            {"PATH": "/usr/bin", "DATABASE_URL": "postgres://x", "GITHUB_REPOSITORY": "acme/widget"}
        )
        self.assertEqual(safe["PATH"], "/usr/bin")
        self.assertEqual(safe["DATABASE_URL"], "postgres://x")
        self.assertEqual(safe["GITHUB_REPOSITORY"], "acme/widget")

    def test_the_artifacts_directory_survives(self):
        safe = tools.child_environment({"QA_API_KEY": "secret", "QA_ARTIFACTS_DIR": "/tmp/qa"})
        self.assertEqual(safe, {"QA_ARTIFACTS_DIR": "/tmp/qa"})

    def test_a_command_cannot_read_the_provider_key(self):
        os.environ["QA_API_KEY"] = "provider-secret"
        self.addCleanup(os.environ.pop, "QA_API_KEY", None)

        result = tools.run_bash("echo [$QA_API_KEY]", tempfile.mkdtemp(), timeout=20)

        self.assertIn("[]", result)
        self.assertNotIn("provider-secret", result)


class RunBashTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def test_backgrounded_process_does_not_block(self):
        started = time.monotonic()
        result = tools.run_bash("sleep 30 & echo server-started", self.workspace, timeout=20)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("server-started", result)
        self.assertIn("exit code: 0", result)

    def test_timeout_reports_partial_output(self):
        result = tools.run_bash("echo before-hang; sleep 10", self.workspace, timeout=1)
        self.assertIn("timed out", result)
        self.assertIn("before-hang", result)

    def test_timeout_kills_what_the_command_started(self):
        pidfile = os.path.join(self.workspace, "pid")
        tools.run_bash("sleep 60 & echo $! > %s; sleep 60" % pidfile, self.workspace, timeout=2)

        with open(pidfile) as handle:
            orphan = int(handle.read().strip())
        time.sleep(0.5)
        with self.assertRaises(OSError):
            os.kill(orphan, 0)

    def test_a_fast_command_after_a_timeout_is_not_itself_reported_as_timed_out(self):
        tools.run_bash("sleep 30", self.workspace, timeout=1)

        result = tools.run_bash("echo quick", self.workspace, timeout=30)

        self.assertIn("exit code: 0", result)
        self.assertIn("quick", result)
        self.assertNotIn("timed out", result)

    def test_exit_code_is_reported(self):
        self.assertIn("exit code: 3", tools.run_bash("exit 3", self.workspace, timeout=10))

    def test_long_output_is_truncated_from_the_middle(self):
        result = tools.run_bash("python3 -c \"print('a' * 40000)\"", self.workspace, timeout=30)
        self.assertIn("chars truncated", result)
        self.assertLess(len(result), tools.MAX_OUTPUT_CHARS + 500)


class ReportTest(unittest.TestCase):
    def test_invalid_status_falls_back_to_partial(self):
        status, body = tools.render_report(
            {"status": "probably fine", "changes_tested": "- a", "evidence": "- b"}
        )
        self.assertEqual(status, "PARTIAL")
        self.assertNotIn("### Edge Cases", body)

    def test_sections_are_rendered_in_order(self):
        _, body = tools.render_report(
            {
                "status": "pass",
                "changes_tested": "- a",
                "evidence": "- b",
                "edge_cases": "- c",
                "not_tested": "- d",
            },
            footer="### Artifacts\n\n- `shot.png`",
        )
        headings = [line for line in body.splitlines() if line.startswith("###")]
        self.assertEqual(
            headings,
            [
                "### Changes Tested",
                "### Evidence",
                "### Edge Cases",
                "### Not Tested",
                "### Artifacts",
            ],
        )

    def test_parse_arguments(self):
        self.assertEqual(tools.parse_arguments('{"a": 1}'), {"a": 1})
        self.assertEqual(tools.parse_arguments({"a": 1}), {"a": 1})
        self.assertEqual(tools.parse_arguments(""), {})
        with self.assertRaises(ValueError):
            tools.parse_arguments("{nope")
        with self.assertRaises(ValueError):
            tools.parse_arguments("[1, 2]")


class ArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def write(self, name):
        path = os.path.join(self.directory, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write("x")

    def test_empty_directory_renders_nothing(self):
        self.assertEqual(tools.render_artifacts(self.directory, "https://example/run"), "")
        self.assertEqual(tools.render_artifacts("", ""), "")
        self.assertEqual(tools.render_artifacts("/nonexistent", ""), "")

    def test_nested_files_are_listed_relative_to_the_directory(self):
        self.write("shot.png")
        self.write("logs/server.log")

        body = tools.render_artifacts(self.directory, "https://example/run/1")

        self.assertIn("- `shot.png`", body)
        self.assertIn("- `logs/server.log`", body)
        self.assertIn("https://example/run/1", body)

    def test_the_listing_is_capped(self):
        for index in range(60):
            self.write("shot-%02d.png" % index)
        names = tools.collect_artifacts(self.directory, limit=10)
        self.assertEqual(len(names), 11)
        self.assertIn("and 50 more", names[-1])


if __name__ == "__main__":
    unittest.main()
