"""Shared plumbing for the checks: inputs from the environment, outputs to the runner."""

import os
import sys

from qa_agent.config import ConfigError, parse_fail_on  # noqa: F401


def env(name, default=""):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def int_env(name, default):
    raw = env(name, default)
    try:
        return int(float(raw))
    except ValueError:
        raise ConfigError("%s must be a number, got %r" % (name, raw))


def bool_env(name, default="true"):
    return env(name, default).strip().lower() in ("true", "1", "yes")


def lines(value):
    """A newline-separated input as a list, blanks dropped."""
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def globs(value):
    """A glob list input: newline- or comma-separated."""
    return [part.strip() for part in (value or "").replace(",", "\n").splitlines() if part.strip()]


def log(message):
    print(message, flush=True)


def _append(path, text):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


def set_output(name, value):
    _append(os.environ.get("GITHUB_OUTPUT"), "%s=%s\n" % (name, value))


def summarize(text):
    _append(os.environ.get("GITHUB_STEP_SUMMARY"), text.rstrip() + "\n\n")


def table(title, rows):
    """A Markdown results table: rows are (check, ok_or_None, detail)."""
    out = ["### %s" % title, "", "| Check | Result | Detail |", "| --- | --- | --- |"]
    for name, ok, detail in rows:
        mark = "skipped" if ok is None else ("pass" if ok else "**FAIL**")
        out.append("| %s | %s | %s |" % (name, mark, (detail or "").replace("|", "\\|")))
    return "\n".join(out)


def verdict(rows):
    """PASS when every non-skipped row passed, SKIPPED when nothing ran, else FAIL."""
    ran = [ok for _, ok, _ in rows if ok is not None]
    if not ran:
        return "SKIPPED"
    return "PASS" if all(ran) else "FAIL"


def finish(status, fail_on):
    """Publish the status and return the process exit code."""
    set_output("status", status)
    log("status: %s" % status)
    return 1 if status in fail_on else 0


def run(main):
    try:
        sys.exit(main())
    except ConfigError as error:
        print("configuration error: %s" % error, file=sys.stderr)
        sys.exit(2)
