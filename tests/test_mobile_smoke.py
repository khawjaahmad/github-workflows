"""The mobile smoke check, driven against a fake adb and apkanalyzer."""

import stat
import tempfile
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_checks import common, mobile_smoke  # noqa: E402

FAKE_ADB = r"""
echo "adb $*" >> "$(dirname "$0")/calls"
case "$*" in
  "install -r "*) echo "Success" ;;
  "shell monkey -p "*"LAUNCHER 1") [ -n "$FAKE_NO_LAUNCH" ] && exit 1; echo "Events injected: 1" ;;
  "shell monkey -p "*) [ -n "$FAKE_MONKEY_CRASH" ] && { echo "// CRASH: com.example"; exit 1; }; echo "Events injected: $FAKE_EVENTS" ;;
  "shell pidof "*) [ -n "$FAKE_DEAD" ] && exit 1; echo 4242 ;;
  "logcat -d -b crash") [ -n "$FAKE_CRASH" ] && echo "FATAL EXCEPTION: main" ;;
  "exec-out screencap -p") printf 'PNG' ;;
  *) ;;
esac
exit 0
"""
FAKE_APKANALYZER = r"""
echo "apkanalyzer $*" >> "$(dirname "$0")/calls"
case "$1 $2" in
  "manifest application-id") echo "com.example.app" ;;
  "apk file-size") echo 123456 ;;
esac
"""


class Run:
    def __init__(self, tools=None, **inputs):
        self.workspace = tempfile.mkdtemp()
        self.artifacts = os.path.join(self.workspace, "artifacts")
        self.tools = tools or make_tools()
        with open(os.path.join(self.workspace, "app.apk"), "wb") as handle:
            handle.write(b"PK")
        self.outputs = os.path.join(self.workspace, "outputs")
        self.summary = os.path.join(self.workspace, "summary")
        env = {
            "QA_WORKSPACE": self.workspace,
            "QA_ARTIFACTS_DIR": self.artifacts,
            "GITHUB_OUTPUT": self.outputs,
            "GITHUB_STEP_SUMMARY": self.summary,
            "PATH": self.tools + os.pathsep + os.environ["PATH"],
            "QA_MOBILE_APP_PATH": "app.apk",
            "QA_MOBILE_LAUNCH_WAIT": "0",
            "QA_MOBILE_MONKEY_EVENTS": "50",
        }
        env.update({"QA_MOBILE_%s" % key.upper(): value for key, value in inputs.items()})
        with mock.patch.dict(os.environ, env, clear=False):
            self.exit_code = mobile_smoke.main()

    def output(self, name):
        with open(self.outputs, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()

    def summary_text(self):
        with open(self.summary, encoding="utf-8") as handle:
            return handle.read()

    def calls(self):
        with open(os.path.join(self.tools, "calls"), encoding="utf-8") as handle:
            return handle.read()


def make_tools(adb=FAKE_ADB, apkanalyzer=FAKE_APKANALYZER):
    directory = tempfile.mkdtemp()
    for name, script in (("adb", adb), ("apkanalyzer", apkanalyzer)):
        if script is None:
            continue
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/bash\n" + script)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return directory


class MobileSmokeTests(unittest.TestCase):
    def test_no_app_is_skipped(self):
        with mock.patch.dict(os.environ, {"QA_MOBILE_APP_PATH": "", "GITHUB_OUTPUT": ""}):
            self.assertEqual(mobile_smoke.main(), 0)

    def test_healthy_app_passes(self):
        run = Run()
        self.assertEqual((run.exit_code, run.output("status")), (0, "PASS"))
        self.assertEqual(run.output("package"), "com.example.app")
        text = run.summary_text()
        self.assertIn("| package | pass | com.example.app (123456 bytes) |", text)
        self.assertIn("| install | pass |", text)
        self.assertIn("| launch | pass | process running", text)
        self.assertIn("| monkey | pass | 50 events", text)
        calls = run.calls()
        self.assertIn("adb install -r app.apk", calls)
        self.assertIn(
            "adb shell monkey -p com.example.app -c android.intent.category.LAUNCHER 1", calls
        )
        self.assertIn("--throttle 300 -s 42 --ignore-security-exceptions -v 50", calls)
        for name in ("launch.png", "monkey.png", "crash-launch.txt", "monkey.txt", "logcat.txt"):
            self.assertTrue(os.path.exists(os.path.join(run.artifacts, name)), name)

    def test_an_explicit_package_skips_apk_inspection(self):
        run = Run(package="org.other")
        self.assertEqual(run.output("package"), "org.other")
        self.assertNotIn("manifest application-id", run.calls())

    def test_a_crash_on_launch_fails(self):
        with mock.patch.dict(os.environ, {"FAKE_CRASH": "1"}):
            run = Run()
        self.assertEqual((run.exit_code, run.output("status")), (1, "FAIL"))
        self.assertIn("FATAL EXCEPTION in logcat", run.summary_text())
        self.assertNotIn("| monkey |", run.summary_text())

    def test_a_process_that_dies_fails(self):
        with mock.patch.dict(os.environ, {"FAKE_DEAD": "1"}):
            run = Run()
        self.assertIn("| launch | **FAIL** | process gone", run.summary_text())

    def test_a_monkey_crash_fails(self):
        with mock.patch.dict(os.environ, {"FAKE_MONKEY_CRASH": "1"}):
            run = Run()
        self.assertEqual(run.output("status"), "FAIL")
        self.assertIn("// CRASH", run.summary_text())

    def test_monkey_can_be_disabled_and_maestro_is_optional(self):
        run = Run(monkey_events="0", flows_dir="flows")
        self.assertNotIn("| monkey |", run.summary_text())
        self.assertIn("| maestro | skipped | maestro is not installed |", run.summary_text())

    def test_unreadable_package_fails_early(self):
        run = Run(
            tools=make_tools(
                apkanalyzer='echo "apkanalyzer $*" >> "$(dirname "$0")/calls"; exit 1\n'
            )
        )
        self.assertEqual(run.output("status"), "FAIL")
        self.assertIn("could not read the application id", run.summary_text())
        self.assertNotIn("adb install", run.calls())

    def test_missing_apk_is_a_config_error(self):
        with mock.patch.dict(
            os.environ, {"QA_MOBILE_APP_PATH": "nope.apk", "QA_WORKSPACE": tempfile.mkdtemp()}
        ):
            with self.assertRaises(common.ConfigError):
                mobile_smoke.main()


if __name__ == "__main__":
    unittest.main()
