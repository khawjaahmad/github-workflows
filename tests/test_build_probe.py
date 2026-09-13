"""The build probe: which build system a checkout has, and what to run."""

import json
import tempfile
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_checks import build_probe  # noqa: E402


class BuildProbeTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def touch(self, name, content=""):
        path = os.path.join(self.workspace, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def test_nothing_recognised(self):
        self.touch("README.md", "# hi")
        self.assertEqual(build_probe.probe(self.workspace)["detected"], "")

    def test_override_wins(self):
        self.touch("go.mod", "module x")
        result = build_probe.probe(self.workspace, "make release")
        self.assertEqual((result["detected"], result["build_command"]), ("custom", "make release"))

    def test_makefile_needs_a_build_target(self):
        self.touch("Makefile", "test:\n\techo\n")
        self.touch("go.mod", "module x")
        self.assertEqual(build_probe.probe(self.workspace)["detected"], "go")
        self.touch("Makefile", "build: deps\n\tgo build\n")
        self.assertEqual(build_probe.probe(self.workspace)["build_command"], "make build")

    def test_node_picks_the_package_manager_from_the_lockfile(self):
        self.touch("package.json", json.dumps({"scripts": {"build": "vite build"}}))
        self.touch("pnpm-lock.yaml")
        result = build_probe.probe(self.workspace)
        self.assertEqual(result["toolchain"], "node")
        self.assertEqual(
            result["build_command"],
            "corepack enable && pnpm install --frozen-lockfile && pnpm run build",
        )

    def test_node_without_a_build_script_only_installs(self):
        self.touch("package.json", json.dumps({"name": "x"}))
        self.touch("package-lock.json")
        self.assertEqual(build_probe.probe(self.workspace)["build_command"], "npm ci")

    def test_node_with_broken_package_json_still_installs(self):
        self.touch("package.json", "{not json")
        self.assertEqual(build_probe.probe(self.workspace)["build_command"], "npm install")

    def test_python_needs_a_project_or_build_system_table(self):
        self.touch("pyproject.toml", "[tool.black]\nline-length = 100\n")
        self.assertEqual(build_probe.probe(self.workspace)["detected"], "")
        self.touch("requirements.txt", "requests\n")
        self.assertEqual(
            build_probe.probe(self.workspace)["build_command"],
            "python -m pip install -r requirements.txt",
        )
        self.touch("pyproject.toml", "[project]\nname = 'x'\n")
        self.assertEqual(
            build_probe.probe(self.workspace)["build_command"], "python -m pip install ."
        )

    def test_wrappers_are_preferred(self):
        self.touch("pom.xml")
        self.assertEqual(
            build_probe.probe(self.workspace)["build_command"], "mvn -B -DskipTests package"
        )
        self.touch("mvnw")
        self.assertEqual(
            build_probe.probe(self.workspace)["build_command"], "./mvnw -B -DskipTests package"
        )
        os.remove(os.path.join(self.workspace, "pom.xml"))
        self.touch("build.gradle.kts")
        self.touch("gradlew")
        self.assertEqual(build_probe.probe(self.workspace)["build_command"], "./gradlew assemble")

    def test_dockerfile_is_the_last_resort(self):
        self.touch("Dockerfile", "FROM scratch")
        self.assertEqual(build_probe.probe(self.workspace)["detected"], "docker")
        self.touch("Cargo.toml", "[package]")
        self.assertEqual(build_probe.probe(self.workspace)["detected"], "rust")

    def test_every_rule_has_a_command(self):
        for name, content in (
            ("go.mod", "module x"),
            ("Gemfile", ""),
            ("mix.exs", ""),
            ("MODULE.bazel", ""),
            ("app.csproj", ""),
        ):
            workspace = tempfile.mkdtemp()
            with open(os.path.join(workspace, name), "w") as handle:
                handle.write(content)
            result = build_probe.probe(workspace)
            self.assertTrue(result["detected"], name)
            self.assertTrue(result["build_command"], name)


class BuildProbeMainTests(unittest.TestCase):
    def test_outputs_and_skip_summary(self):
        workspace = tempfile.mkdtemp()
        outputs = os.path.join(workspace, "out")
        summary = os.path.join(workspace, "summary")
        env = {
            "QA_WORKSPACE": workspace,
            "GITHUB_OUTPUT": outputs,
            "GITHUB_STEP_SUMMARY": summary,
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(build_probe.main(), 0)
        self.assertIn("detected=\n", open(outputs).read())
        self.assertIn("Skipping build", open(summary).read())


if __name__ == "__main__":
    unittest.main()
