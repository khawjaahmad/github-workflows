"""Install an APK on the running emulator, launch it, read the crash log, poke it."""

import os
import re
import shutil
import time
from dataclasses import dataclass

from qa_agent import tools

from . import common, server

CRASH_MARKERS = ("FATAL EXCEPTION", "ANR in", "Force finishing activity")
MONKEY_MARKERS = ("// CRASH", "// NOT RESPONDING")


@dataclass(frozen=True)
class Config:
    workspace: str
    app_path: str
    package: str
    monkey_events: int
    flows_dir: str
    launch_wait: int
    command_timeout: int
    artifacts_dir: str
    fail_on: tuple


def from_env():
    return Config(
        workspace=common.env("QA_WORKSPACE", os.getcwd()),
        app_path=common.env("QA_MOBILE_APP_PATH"),
        package=common.env("QA_MOBILE_PACKAGE"),
        monkey_events=common.int_env("QA_MOBILE_MONKEY_EVENTS", "2000"),
        flows_dir=common.env("QA_MOBILE_FLOWS_DIR"),
        launch_wait=common.int_env("QA_MOBILE_LAUNCH_WAIT", "10"),
        command_timeout=common.int_env("QA_MOBILE_COMMAND_TIMEOUT", "600"),
        artifacts_dir=common.env("QA_ARTIFACTS_DIR"),
        fail_on=common.parse_fail_on(common.env("QA_MOBILE_FAIL_ON", "FAIL")),
    )


def main():
    config = from_env()
    if not config.app_path:
        common.summarize("Skipping mobile smoke: set `app_path` to an APK.")
        return common.finish("SKIPPED", config.fail_on)
    if not os.path.isfile(os.path.join(config.workspace, config.app_path)):
        raise common.ConfigError("app_path %r does not exist" % config.app_path)
    artifacts = config.artifacts_dir or os.path.join(config.workspace, "mobile-artifacts")
    os.makedirs(artifacts, exist_ok=True)

    rows = []
    package = config.package or _package(config)
    if not package:
        rows.append(("package", False, "could not read the application id; set `package`"))
    else:
        rows.append(("package", True, "%s (%s)" % (package, _size(config))))
        rows.append(server.command("install", "adb install -r %s" % config.app_path, config))
    if common.healthy(rows):
        rows += _launch(config, package, artifacts)
    if common.healthy(rows) and config.monkey_events > 0:
        rows.append(_monkey(config, package, artifacts))
    if common.healthy(rows) and config.flows_dir:
        rows.append(_maestro(config, artifacts))
    _run("adb logcat -d > %s" % os.path.join(artifacts, "logcat.txt"), config)

    status = common.verdict(rows)
    common.summarize(common.table("Mobile smoke (%s): %s" % (config.app_path, status), rows))
    common.log_rows(rows)
    common.set_output("package", package or "")
    return common.finish(status, config.fail_on)


def _run(command_line, config):
    return tools.execute(command_line, config.workspace, config.command_timeout)


def _apkanalyzer():
    found = shutil.which("apkanalyzer")
    if found:
        return found
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
    candidate = os.path.join(home, "cmdline-tools", "latest", "bin", "apkanalyzer")
    return candidate if os.path.isfile(candidate) else ""


def _package(config):
    tool = _apkanalyzer()
    if tool:
        code, output = _run("%s manifest application-id %s" % (tool, config.app_path), config)
        if code == 0 and output.strip():
            return output.strip().splitlines()[-1].strip()
    code, output = _run("aapt2 dump badging %s" % config.app_path, config)
    match = re.search(r"package: name='([^']+)'", output)
    return match.group(1) if match else ""


def _size(config):
    tool = _apkanalyzer()
    if not tool:
        return "size unknown"
    code, output = _run("%s apk file-size %s" % (tool, config.app_path), config)
    return "%s bytes" % output.strip() if code == 0 and output.strip().isdigit() else "size unknown"


def _launch(config, package, artifacts):
    _run("adb logcat -c", config)
    code, output = _run(
        "adb shell monkey -p %s -c android.intent.category.LAUNCHER 1" % package, config
    )
    if code != 0:
        return [("launch", False, "launcher intent failed: %s" % server.tail(output))]
    time.sleep(config.launch_wait)
    _run("adb exec-out screencap -p > %s" % os.path.join(artifacts, "launch.png"), config)
    code, pid = _run("adb shell pidof %s" % package, config)
    alive = code == 0 and pid.strip() != ""
    rows = [
        (
            "launch",
            alive,
            "process %s after %ds" % ("running" if alive else "gone", config.launch_wait),
        )
    ]
    rows.append(_crash_row("crash log", config, artifacts, "launch"))
    return rows


def _crash_row(name, config, artifacts, phase):
    _, crash = _run("adb logcat -d -b crash", config)
    _, main = _run("adb logcat -d -s ActivityManager:E AndroidRuntime:E", config)
    text = crash + "\n" + main
    with open(os.path.join(artifacts, "crash-%s.txt" % phase), "w", encoding="utf-8") as handle:
        handle.write(text)
    hits = [marker for marker in CRASH_MARKERS if marker in text]
    if hits:
        return (name, False, "%s in logcat; see crash-%s.txt" % (", ".join(hits), phase))
    return (name, True, "no crash or ANR in logcat")


def _monkey(config, package, artifacts):
    _run("adb logcat -c", config)
    code, output = _run(
        "adb shell monkey -p %s --throttle 300 -s 42 --ignore-security-exceptions -v %d"
        % (package, config.monkey_events),
        config,
    )
    with open(os.path.join(artifacts, "monkey.txt"), "w", encoding="utf-8") as handle:
        handle.write(output)
    _run("adb exec-out screencap -p > %s" % os.path.join(artifacts, "monkey.png"), config)
    hits = [marker for marker in MONKEY_MARKERS if marker in output]
    if code != 0 or hits:
        return (
            "monkey",
            False,
            "%d events: exit %s%s; see monkey.txt"
            % (config.monkey_events, code, ", " + ", ".join(hits) if hits else ""),
        )
    crash = _crash_row("monkey", config, artifacts, "monkey")
    return ("monkey", crash[1], "%d events, %s" % (config.monkey_events, crash[2]))


def _maestro(config, artifacts):
    if not shutil.which("maestro"):
        return ("maestro", None, "maestro is not installed")
    report = os.path.join(artifacts, "maestro-junit.xml")
    return server.command(
        "maestro",
        "maestro test --format junit --output %s %s" % (report, config.flows_dir),
        config,
    )


if __name__ == "__main__":
    common.run(main)
