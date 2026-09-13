"""The web check wrapper: server lifecycle, config hand-off to check.mjs, result rendering."""

import json
import socket
import tempfile
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_checks import common, web_check  # noqa: E402

FAKE_SCRIPT = """
import fs from "node:fs";
const [configPath, resultsPath] = process.argv.slice(2);
const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
fs.writeFileSync(configPath + ".seen", JSON.stringify(config));
if (process.env.FAKE_CRASH) { console.error("boom"); process.exit(3); }
const rows = [["routes", true, config.url], ["route /", !process.env.FAKE_FAIL, "checked"]];
fs.writeFileSync(resultsPath, JSON.stringify({ rows }));
"""


class Run:
    def __init__(self, **inputs):
        self.workspace = tempfile.mkdtemp()
        self.artifacts = os.path.join(self.workspace, "artifacts")
        script_dir = tempfile.mkdtemp()
        with open(os.path.join(script_dir, "check.mjs"), "w", encoding="utf-8") as handle:
            handle.write(FAKE_SCRIPT)
        self.outputs = os.path.join(self.workspace, "outputs")
        self.summary = os.path.join(self.workspace, "summary")
        env = {
            "QA_WORKSPACE": self.workspace,
            "QA_ARTIFACTS_DIR": self.artifacts,
            "QA_WEB_SCRIPT_DIR": script_dir,
            "GITHUB_OUTPUT": self.outputs,
            "GITHUB_STEP_SUMMARY": self.summary,
        }
        env.update({"QA_WEB_%s" % key.upper(): value for key, value in inputs.items()})
        with mock.patch.dict(os.environ, env, clear=False):
            self.exit_code = web_check.main()

    def output(self, name):
        with open(self.outputs, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()

    def summary_text(self):
        with open(self.summary, encoding="utf-8") as handle:
            return handle.read()

    def config_seen(self):
        with open(os.path.join(self.artifacts, "web-check-config.json.seen")) as handle:
            return json.load(handle)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class WebCheckTests(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.url = "http://127.0.0.1:%d/" % self.port
        self.serve = "%s -m http.server %d --bind 127.0.0.1" % (sys.executable, self.port)

    def test_nothing_configured_is_skipped(self):
        run = Run()
        self.assertEqual((run.exit_code, run.output("status")), (0, "SKIPPED"))
        self.assertIn("Skipping web check", run.summary_text())

    def test_started_site_is_checked_with_the_config_handed_over(self):
        run = Run(
            start_command=self.serve, port=str(self.port), routes="/\n/a", lighthouse_budget="90"
        )
        self.assertEqual((run.exit_code, run.output("status")), (0, "PASS"))
        self.assertEqual(run.output("url"), self.url)
        seen = run.config_seen()
        self.assertEqual(seen["url"], self.url)
        self.assertEqual(seen["routes"], ["/", "/a"])
        self.assertEqual(seen["lighthouseBudget"], 90)
        self.assertTrue(seen["accessibility"])
        self.assertIn("| ready | pass |", run.summary_text())
        self.assertIn("| route / | pass |", run.summary_text())

    def test_a_given_url_is_used_without_starting_anything(self):
        run = Run(url="http://example.invalid:1/")
        self.assertEqual(run.config_seen()["url"], "http://example.invalid:1/")
        self.assertNotIn("| ready |", run.summary_text())

    def test_failing_rows_fail_the_run(self):
        with mock.patch.dict(os.environ, {"FAKE_FAIL": "1"}):
            run = Run(url="http://example.invalid:1/")
        self.assertEqual((run.exit_code, run.output("status")), (1, "FAIL"))
        with mock.patch.dict(os.environ, {"FAKE_FAIL": "1"}):
            run = Run(url="http://example.invalid:1/", fail_on="none")
        self.assertEqual((run.exit_code, run.output("status")), (0, "FAIL"))

    def test_a_crashing_script_is_one_failed_row_with_its_log(self):
        with mock.patch.dict(os.environ, {"FAKE_CRASH": "1"}):
            run = Run(url="http://example.invalid:1/")
        self.assertEqual(run.output("status"), "FAIL")
        self.assertIn("check.mjs exit 3", run.summary_text())
        with open(os.path.join(run.artifacts, "web-check.log")) as handle:
            self.assertIn("boom", handle.read())

    def test_a_dead_server_never_reaches_the_browser(self):
        run = Run(start_command="exit 9", port=str(self.port), ready_timeout="5")
        self.assertEqual(run.output("status"), "FAIL")
        self.assertIn("exited with code 9", run.summary_text())
        self.assertFalse(os.path.exists(os.path.join(run.artifacts, "web-check-config.json.seen")))

    def test_start_command_needs_a_port(self):
        with mock.patch.dict(os.environ, {"QA_WEB_START_COMMAND": "x", "QA_WEB_PORT": ""}):
            with self.assertRaises(common.ConfigError):
                web_check.from_env()


if __name__ == "__main__":
    unittest.main()
