"""Probing an unfamiliar repository."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_agent import environment  # noqa: E402


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def write(self, name, content="x"):
        path = os.path.join(self.workspace, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)

    def test_reports_the_build_files_it_finds(self):
        self.write("go.mod")
        self.write("Makefile")

        report = environment.probe(self.workspace)

        self.assertIn("go.mod (Go)", report)
        self.assertIn("Makefile (make)", report)
        self.assertNotIn("package.json", report)

    def test_says_so_when_the_stack_is_unrecognised(self):
        self.write("main.zig")
        report = environment.probe(self.workspace)
        self.assertIn("none found", report)
        # The listing still gives the agent somewhere to start.
        self.assertIn("main.zig", report)

    def test_lists_the_tooling_actually_installed(self):
        report = environment.probe(self.workspace)
        self.assertIn("Commands on PATH:", report)
        self.assertIn("git", report)

    def test_names_the_documentation_worth_reading(self):
        self.write("AGENTS.md")
        self.assertIn("AGENTS.md", environment.probe(self.workspace))

    def test_survives_a_missing_workspace(self):
        report = environment.probe(os.path.join(self.workspace, "gone"))
        self.assertIn("Top-level entries: (empty)", report)


class GuideTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def write_guide(self, content):
        path = os.path.join(self.workspace, ".agents", "skills", "qa-guide.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(content)

    def test_absent_guide_is_empty(self):
        self.assertEqual(environment.read_guide(self.workspace), "")

    def test_guide_is_read(self):
        self.write_guide("# QA Guidelines\n\nRun `make setup`.")
        self.assertIn("make setup", environment.read_guide(self.workspace))

    def test_long_guide_is_truncated(self):
        self.write_guide("g" * 500)
        guide = environment.read_guide(self.workspace, max_chars=100)
        self.assertIn("truncated", guide)
        self.assertLess(len(guide), 200)


if __name__ == "__main__":
    unittest.main()
