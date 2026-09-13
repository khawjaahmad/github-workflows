"""The API contract check: spec detection, lint and breaking-change wiring, live checks."""

import socket
import stat
import subprocess
import tempfile
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_checks import api_contract, common  # noqa: E402


def fake_tool(directory, name, script):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/bash\n" + script)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def git(workspace, *args):
    subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)


class Run:
    """Runs api_contract.main() in a workspace with fake tools on PATH."""

    def __init__(self, workspace, tools, **inputs):
        self.outputs = os.path.join(workspace, "outputs")
        self.summary = os.path.join(workspace, "summary")
        env = {
            "QA_WORKSPACE": workspace,
            "QA_ARTIFACTS_DIR": os.path.join(workspace, "artifacts"),
            "GITHUB_OUTPUT": self.outputs,
            "GITHUB_STEP_SUMMARY": self.summary,
            "GITHUB_BASE_REF": "",
            "PATH": tools + os.pathsep + os.environ["PATH"],
        }
        env.update({"QA_API_%s" % key.upper(): value for key, value in inputs.items()})
        with mock.patch.dict(os.environ, env, clear=False):
            self.exit_code = api_contract.main()

    def output(self, name):
        with open(self.outputs, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()

    def summary_text(self):
        with open(self.summary, encoding="utf-8") as handle:
            return handle.read()


class DetectionTests(unittest.TestCase):
    def test_detect_spec_prefers_openapi_over_graphql(self):
        workspace = tempfile.mkdtemp()
        self.assertEqual(api_contract.detect_spec(workspace), "")
        open(os.path.join(workspace, "schema.graphql"), "w").close()
        self.assertEqual(api_contract.detect_spec(workspace), "schema.graphql")
        open(os.path.join(workspace, "openapi.json"), "w").close()
        self.assertEqual(api_contract.detect_spec(workspace), "openapi.json")

    def test_kind(self):
        self.assertEqual(api_contract.kind_of("api/openapi.yaml"), "openapi")
        self.assertEqual(api_contract.kind_of("schema.GraphQL"), "graphql")

    def test_base_ref_resolution(self):
        cases = {
            ("", ""): "",
            ("", "main"): "origin/main",
            ("main", ""): "origin/main",
            ("refs/heads/release/1.x", ""): "origin/release/1.x",
            ("origin/dev", ""): "origin/dev",
            ("HEAD~1", ""): "HEAD~1",
            ("a5f8c2b", ""): "a5f8c2b",
        }
        for (explicit, github), expected in cases.items():
            with mock.patch.dict(os.environ, {"GITHUB_BASE_REF": github}, clear=False):
                self.assertEqual(common.base_ref(explicit), expected, (explicit, github))


class MainTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.tools = tempfile.mkdtemp()
        git(self.workspace, "init", "-q", "-b", "main")
        git(self.workspace, "config", "user.email", "t@e.st")
        git(self.workspace, "config", "user.name", "t")
        self.write("openapi.yaml", "openapi: 3.0.3\npaths: {}\n")
        git(self.workspace, "add", ".")
        git(self.workspace, "commit", "-q", "-m", "base")
        self.write("openapi.yaml", "openapi: 3.0.3\npaths:\n  /x: {}\n")
        fake_tool(
            self.tools,
            "vacuum",
            'echo "vacuum $*" >> "$(dirname "$0")/calls"; exit ${FAKE_VACUUM_EXIT:-0}\n',
        )
        fake_tool(
            self.tools,
            "oasdiff",
            'echo "oasdiff $*" >> "$(dirname "$0")/calls"; exit ${FAKE_OASDIFF_EXIT:-0}\n',
        )

    def write(self, name, content):
        with open(os.path.join(self.workspace, name), "w", encoding="utf-8") as handle:
            handle.write(content)

    def calls(self):
        with open(os.path.join(self.tools, "calls"), encoding="utf-8") as handle:
            return handle.read()

    def test_no_spec_is_skipped(self):
        os.remove(os.path.join(self.workspace, "openapi.yaml"))
        run = Run(self.workspace, self.tools)
        self.assertEqual((run.exit_code, run.output("status")), (0, "SKIPPED"))
        self.assertIn("Skipping API contract", run.summary_text())

    def test_lint_and_breaking_pass(self):
        run = Run(self.workspace, self.tools, base_ref="HEAD")
        self.assertEqual((run.exit_code, run.output("status")), (0, "PASS"))
        self.assertEqual(run.output("kind"), "openapi")
        calls = self.calls()
        self.assertIn("vacuum lint --fail-severity error --no-style --details openapi.yaml", calls)
        self.assertIn("oasdiff breaking --fail-on ERR", calls)
        self.assertIn("base-openapi.yaml openapi.yaml", calls)
        with open(os.path.join(self.workspace, "artifacts", "base-openapi.yaml")) as handle:
            self.assertEqual(handle.read(), "openapi: 3.0.3\npaths: {}\n")

    def test_a_repository_ruleset_is_used(self):
        self.write(".spectral.yaml", "extends: spectral:oas\n")
        Run(self.workspace, self.tools, base_ref="HEAD", breaking="false")
        self.assertIn("-r .spectral.yaml openapi.yaml", self.calls())

    def test_breaking_change_fails(self):
        with mock.patch.dict(os.environ, {"FAKE_OASDIFF_EXIT": "1"}):
            run = Run(self.workspace, self.tools, base_ref="HEAD")
        self.assertEqual((run.exit_code, run.output("status")), (1, "FAIL"))
        self.assertIn("| breaking | **FAIL** |", run.summary_text())

    def test_new_spec_has_nothing_to_compare(self):
        self.write("api.yaml", "openapi: 3.0.3\n")
        run = Run(self.workspace, self.tools, base_ref="HEAD", spec_path="api.yaml", lint="false")
        self.assertEqual(run.output("status"), "SKIPPED")
        self.assertIn("nothing to compare", run.summary_text())

    def test_no_base_ref_skips_breaking(self):
        run = Run(self.workspace, self.tools, lint="false")
        self.assertIn("no base ref", run.summary_text())

    def test_missing_tools_are_reported_not_fatal(self):
        run = Run(self.workspace, tempfile.mkdtemp(), base_ref="HEAD")
        self.assertEqual(run.output("status"), "SKIPPED")
        self.assertIn("vacuum is not installed", run.summary_text())
        self.assertIn("oasdiff is not installed", run.summary_text())

    def test_graphql_uses_graphql_inspector(self):
        fake_tool(self.tools, "npx", 'echo "npx $*" >> "$(dirname "$0")/calls"; exit 0\n')
        self.write("schema.graphql", "type Query { a: Int }\n")
        git(self.workspace, "add", "schema.graphql")
        git(self.workspace, "commit", "-q", "-m", "schema")
        run = Run(self.workspace, self.tools, base_ref="HEAD", spec_path="schema.graphql")
        self.assertEqual(run.output("kind"), "graphql")
        self.assertIn("graphql-inspector diff", self.calls())
        self.assertIn("no linter for GraphQL", run.summary_text())

    def test_live_checks_start_the_server_and_fuzz_it(self):
        fake_tool(
            self.tools,
            "schemathesis",
            'echo "schemathesis $*" >> "$(dirname "$0")/calls"; exit 0\n',
        )
        fake_tool(self.tools, "hurl", 'echo "hurl $*" >> "$(dirname "$0")/calls"; exit 0\n')
        os.mkdir(os.path.join(self.workspace, "hurl"))
        port = free_port()
        run = Run(
            self.workspace,
            self.tools,
            lint="false",
            breaking="false",
            start_command="%s -m http.server %d --bind 127.0.0.1" % (sys.executable, port),
            ready_url="http://127.0.0.1:%d/health" % port,
            hurl_dir="hurl",
            auth_header="Authorization: Bearer t",
            max_examples="7",
        )
        self.assertEqual((run.exit_code, run.output("status")), (0, "PASS"))
        self.assertIn(
            "schemathesis run openapi.yaml --url http://127.0.0.1:%d --max-examples 7" % port,
            self.calls(),
        )
        self.assertIn("-H Authorization: Bearer t", self.calls())
        self.assertIn("hurl --test --variable base_url=http://127.0.0.1:%d " % port, self.calls())
        self.assertIn("| ready | pass |", run.summary_text())
        self.assertIn("| hurl | pass |", run.summary_text())

    def test_base_url_wins_over_the_ready_url_origin(self):
        fake_tool(
            self.tools,
            "schemathesis",
            'echo "schemathesis $*" >> "$(dirname "$0")/calls"; exit 0\n',
        )
        Run(
            self.workspace,
            self.tools,
            lint="false",
            breaking="false",
            base_url="http://api.test:9/v2/",
        )
        self.assertIn("--url http://api.test:9/v2 ", self.calls())

    def test_missing_spec_path_is_a_config_error(self):
        with mock.patch.dict(
            os.environ, {"QA_WORKSPACE": self.workspace, "QA_API_SPEC_PATH": "nope.yaml"}
        ):
            with self.assertRaises(common.ConfigError):
                api_contract.main()


if __name__ == "__main__":
    unittest.main()
