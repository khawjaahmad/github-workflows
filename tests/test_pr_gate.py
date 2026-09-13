"""The PR gate: size, paths, TODOs, tests, agent detection."""

import tempfile
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import PULL_REQUEST, GitHubServer  # noqa: E402

from qa_checks import common, pr_gate  # noqa: E402


def config(**overrides):
    env = {"QA_REPO": "acme/widget", "QA_PR_NUMBER": "7", "QA_GITHUB_TOKEN": "t"}
    env.update(overrides)
    with mock.patch.dict(os.environ, env, clear=False):
        return pr_gate.from_env()


def files(*specs):
    return [{"filename": name, "additions": add, "deletions": rem} for name, add, rem in specs]


class CheckTests(unittest.TestCase):
    def test_size_ignores_lockfiles_by_default(self):
        changed = files(("src/a.py", 10, 5), ("package-lock.json", 5000, 0))
        name, ok, detail = pr_gate.check_size(changed, config())
        self.assertTrue(ok)
        self.assertIn("15 lines", detail)
        self.assertFalse(pr_gate.check_size(changed, config(QA_GATE_MAX_LINES="10"))[1])
        self.assertIsNone(pr_gate.check_size(changed, config(QA_GATE_MAX_LINES="0"))[1])

    def test_paths(self):
        changed = files(("src/a.py", 1, 0), (".github/workflows/x.yml", 1, 0))
        self.assertIsNone(pr_gate.check_paths(changed, config())[1])
        name, ok, detail = pr_gate.check_paths(changed, config(QA_GATE_ALLOWED_PATHS="src/**"))
        self.assertFalse(ok)
        self.assertIn(".github/workflows/x.yml", detail)
        self.assertFalse(
            pr_gate.check_paths(changed, config(QA_GATE_FORBIDDEN_PATHS=".github/*"))[1]
        )
        self.assertTrue(pr_gate.check_paths(changed[:1], config(QA_GATE_ALLOWED_PATHS="src/*"))[1])

    def test_todos_only_count_added_lines(self):
        diff = "+++ b/a.py\n-# TODO old\n+# fine\n+x = 1  # FIXME later\n"
        name, ok, detail = pr_gate.check_todos(diff, config())
        self.assertFalse(ok)
        self.assertIn("FIXME later", detail)
        self.assertTrue(pr_gate.check_todos("+++ b/a\n-# TODO\n", config())[1])
        self.assertIsNone(pr_gate.check_todos(diff, config(QA_GATE_NO_NEW_TODOS="false"))[1])

    def test_tests_required_for_touched_sources(self):
        rule = config(QA_GATE_REQUIRE_TESTS_FOR="src/*")
        self.assertIsNone(pr_gate.check_tests([], config())[1])
        self.assertTrue(pr_gate.check_tests(files(("docs/a.md", 1, 0)), rule)[1])
        self.assertFalse(pr_gate.check_tests(files(("src/a.py", 1, 0)), rule)[1])
        self.assertTrue(
            pr_gate.check_tests(files(("src/a.py", 1, 0), ("tests/test_a.py", 1, 0)), rule)[1]
        )

    def test_agent_detection(self):
        rule = config()
        self.assertTrue(
            pr_gate.is_agent_pr({"user": {"login": "Copilot-SWE-Agent[bot]"}}, [], rule)
        )
        self.assertFalse(pr_gate.is_agent_pr({"user": {"login": "alice"}}, [], rule))
        commits = [{"commit": {"message": "fix\n\nCo-Authored-By: Claude <noreply@anthropic.com>"}}]
        self.assertTrue(pr_gate.is_agent_pr({"user": {"login": "alice"}}, commits, rule))
        self.assertTrue(
            pr_gate.is_agent_pr(
                {"user": {"login": "mybot[bot]"}}, [], config(QA_GATE_AGENT_LOGINS="mybot[bot]")
            )
        )


class MainTests(unittest.TestCase):
    def run_main(self, server, **overrides):
        workspace = tempfile.mkdtemp()
        outputs = os.path.join(workspace, "outputs")
        summary = os.path.join(workspace, "summary")
        env = {
            "QA_REPO": "acme/widget",
            "QA_PR_NUMBER": "7",
            "QA_GITHUB_TOKEN": "t",
            "QA_GITHUB_API_URL": server.url,
            "GITHUB_OUTPUT": outputs,
            "GITHUB_STEP_SUMMARY": summary,
        }
        env.update(overrides)
        with mock.patch.dict(os.environ, env, clear=False):
            code = pr_gate.main()
        return code, open(outputs).read(), open(summary).read()

    def test_clean_pr_passes_and_labels_an_agent(self):
        pull_request = dict(PULL_REQUEST, user={"login": "copilot-swe-agent[bot]"})
        server = GitHubServer(
            pull_request=pull_request,
            files=files(("src/a.py", 3, 1), (".github/workflows/ci.yml", 1, 0)),
            diff="+++ b/src/a.py\n+x = 1\n",
        ).start()
        code, outputs, summary = self.run_main(server)
        self.assertEqual(code, 0)
        self.assertIn("status=PASS", outputs)
        self.assertIn("is_agent_pr=true", outputs)
        self.assertIn("workflows_changed=true", outputs)
        self.assertEqual(server.labels, ["ai-generated"])
        self.assertIn("| agent label | pass |", summary)

    def test_failures_are_reported_but_never_exit_non_zero(self):
        server = GitHubServer(files=files(("src/a.py", 3000, 0)), diff="+++ b/a\n+# TODO\n").start()
        code, outputs, summary = self.run_main(server)
        self.assertEqual(code, 0)
        self.assertIn("status=FAIL", outputs)
        self.assertIn("| size | **FAIL** |", summary)
        self.assertIn("| todos | **FAIL** |", summary)
        self.assertEqual(server.labels, [])

    def test_dependency_graph_is_probed_when_asked(self):
        server = GitHubServer(files=files(("a", 1, 0)), graph_enabled=False).start()
        code, outputs, summary = self.run_main(server, QA_GATE_DEPENDENCY_REVIEW="true")
        self.assertIn("dependency_graph=false", outputs)
        self.assertIn("dependency graph is not enabled", summary)
        server = GitHubServer(files=files(("a", 1, 0)), graph_enabled=True).start()
        code, outputs, summary = self.run_main(server, QA_GATE_DEPENDENCY_REVIEW="true")
        self.assertIn("dependency_graph=true", outputs)

    def test_bad_pr_number_is_a_config_error(self):
        with self.assertRaises(common.ConfigError):
            config(QA_PR_NUMBER="")


if __name__ == "__main__":
    unittest.main()
