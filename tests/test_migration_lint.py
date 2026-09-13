"""The migration lint: file selection by scope and glob, squawk wiring, the drift check."""

import stat
import subprocess
import tempfile
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_checks import common, migration_lint  # noqa: E402


def git(workspace, *args):
    subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)


class Run:
    def __init__(self, workspace, tools, **inputs):
        self.outputs = os.path.join(workspace, "outputs")
        self.summary = os.path.join(workspace, "summary")
        env = {
            "QA_WORKSPACE": workspace,
            "GITHUB_OUTPUT": self.outputs,
            "GITHUB_STEP_SUMMARY": self.summary,
            "GITHUB_BASE_REF": "",
            "PATH": tools + os.pathsep + os.environ["PATH"],
        }
        env.update({"QA_MIGRATION_%s" % key.upper(): value for key, value in inputs.items()})
        with mock.patch.dict(os.environ, env, clear=False):
            self.exit_code = migration_lint.main()

    def output(self, name):
        with open(self.outputs, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()

    def summary_text(self):
        with open(self.summary, encoding="utf-8") as handle:
            return handle.read()


class MigrationLintTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.tools = tempfile.mkdtemp()
        path = os.path.join(self.tools, "squawk")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                '#!/bin/bash\necho "squawk $*" >> "$(dirname "$0")/calls"; exit ${FAKE_SQUAWK_EXIT:-0}\n'
            )
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        git(self.workspace, "init", "-q", "-b", "main")
        git(self.workspace, "config", "user.email", "t@e.st")
        git(self.workspace, "config", "user.name", "t")
        self.write("db/migrations/0001_init.sql", "create table a (id int);")
        self.write("README.md", "hi")
        git(self.workspace, "add", ".")
        git(self.workspace, "commit", "-q", "-m", "base")
        self.write("db/migrations/0002_more.sql", "alter table a add b int;")
        self.write("src/app.py", "x = 1")
        git(self.workspace, "add", ".")
        git(self.workspace, "commit", "-q", "-m", "head")

    def write(self, name, content):
        path = os.path.join(self.workspace, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def calls(self):
        with open(os.path.join(self.tools, "calls"), encoding="utf-8") as handle:
            return handle.read()

    def test_changed_scope_lints_only_the_new_migration(self):
        run = Run(self.workspace, self.tools, base_ref="HEAD~1")
        self.assertEqual((run.exit_code, run.output("status")), (0, "PASS"))
        self.assertEqual(run.output("files"), "1")
        self.assertEqual(self.calls().strip(), "squawk db/migrations/0002_more.sql")

    def test_all_scope_lints_every_migration(self):
        run = Run(self.workspace, self.tools, scope="all")
        self.assertEqual(run.output("files"), "2")
        self.assertIn("db/migrations/0001_init.sql db/migrations/0002_more.sql", self.calls())

    def test_no_changed_sql_is_skipped(self):
        self.write("src/other.py", "y = 2")
        git(self.workspace, "add", ".")
        git(self.workspace, "commit", "-q", "-m", "code only")
        run = Run(self.workspace, self.tools, base_ref="HEAD~1")
        self.assertEqual((run.exit_code, run.output("status")), (0, "SKIPPED"))
        self.assertIn("Skipping migration lint", run.summary_text())

    def test_squawk_findings_fail(self):
        with mock.patch.dict(os.environ, {"FAKE_SQUAWK_EXIT": "1"}):
            run = Run(self.workspace, self.tools, base_ref="HEAD~1")
        self.assertEqual((run.exit_code, run.output("status")), (1, "FAIL"))
        self.assertIn("| lint | **FAIL** | squawk over 1 file(s)", run.summary_text())

    def test_check_command_runs_even_without_sql(self):
        run = Run(
            self.workspace,
            self.tools,
            scope="all",
            sql_globs="nothing/*.sql",
            check_command="exit 3",
        )
        self.assertEqual(run.output("status"), "FAIL")
        self.assertIn("| check | **FAIL** | `exit 3` exit 3", run.summary_text())

    def test_other_dialects_skip_the_linter(self):
        run = Run(self.workspace, self.tools, scope="all", dialect="mysql")
        self.assertEqual(run.output("status"), "SKIPPED")
        self.assertIn("no linter for dialect 'mysql'", run.summary_text())

    def test_missing_base_ref_is_reported(self):
        run = Run(self.workspace, self.tools)
        self.assertEqual(run.output("status"), "FAIL")
        self.assertIn("no base ref", run.summary_text())

    def test_bad_scope_is_a_config_error(self):
        with mock.patch.dict(os.environ, {"QA_MIGRATION_SCOPE": "some"}):
            with self.assertRaises(common.ConfigError):
                migration_lint.from_env()


if __name__ == "__main__":
    unittest.main()
