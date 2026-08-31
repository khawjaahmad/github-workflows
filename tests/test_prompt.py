"""The opening message the agent is given."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import PULL_REQUEST, make_config  # noqa: E402

from qa_agent import prompt  # noqa: E402


class InitialMessageTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def message(self, **overrides):
        overrides.setdefault("workspace", self.workspace)
        return prompt.initial_message(PULL_REQUEST, "diff --git a/a b/a", make_config(**overrides))

    def test_carries_the_pull_request_context(self):
        message = self.message()
        self.assertIn("Add /api/health endpoint", message)
        self.assertIn("Returns 200 with the version.", message)
        self.assertIn("diff --git a/a b/a", message)

    def test_gives_the_shas_needed_for_a_before_and_after(self):
        message = self.message()
        self.assertIn("git checkout base1234", message)
        self.assertIn("git checkout head5678", message)
        self.assertIn("git fetch --no-tags origin main", message)

    def test_survives_a_pull_request_without_shas(self):
        message = prompt.initial_message({"number": 1}, "", make_config(workspace=self.workspace))
        self.assertIn("sha unknown", message)
        self.assertIn("Begin with phase 1", message)

    def test_describes_the_environment_rather_than_assuming_one(self):
        with open(os.path.join(self.workspace, "Cargo.toml"), "w") as handle:
            handle.write("[package]")
        message = self.message()
        self.assertIn("Cargo.toml (Rust)", message)

    def test_includes_the_repository_qa_guide(self):
        path = os.path.join(self.workspace, ".agents", "skills", "qa-guide.md")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            handle.write("Always check /admin.")

        message = self.message()

        self.assertIn("Always check /admin.", message)
        self.assertIn("authoritative", message)

    def test_mentions_the_artifacts_directory_only_when_there_is_one(self):
        self.assertNotIn("## Artifacts", self.message())
        self.assertIn("/tmp/qa-artifacts", self.message(artifacts_dir="/tmp/qa-artifacts"))

    def test_passes_on_the_setup_command(self):
        self.assertIn("make bootstrap", self.message(setup_command="make bootstrap"))

    def test_the_system_prompt_states_what_is_out_of_scope(self):
        self.assertIn("Do NOT run the test suite", prompt.SYSTEM_PROMPT)
        self.assertIn("Do not assume a language or a package manager", prompt.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
