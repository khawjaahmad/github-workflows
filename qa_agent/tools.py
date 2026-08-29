"""Tools the QA agent can call: run a shell command, and submit the report."""

import json
import subprocess
import tempfile

MAX_OUTPUT_CHARS = 20000
VALID_STATUSES = ("PASS", "FAIL", "PARTIAL")


def definitions(command_timeout):
    return [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": (
                    "Run a shell command in the checked-out repository and return its "
                    "combined stdout/stderr and exit code. Use this to install "
                    "dependencies, build, start servers (append & to background them), "
                    "curl endpoints, run CLI commands and drive browsers. Each call is a "
                    "separate process, so chain with && or cd inside the command."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The command to run with bash -c.",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Seconds to allow, default %d." % command_timeout,
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_report",
                "description": (
                    "Submit the final QA report. Call this exactly once, when testing is "
                    "finished or when you have exhausted your approaches."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": list(VALID_STATUSES),
                            "description": (
                                "PASS when the changed behaviour works as claimed, FAIL when "
                                "it is broken, PARTIAL when some scenarios could not be run."
                            ),
                        },
                        "changes_tested": {
                            "type": "string",
                            "description": "Markdown bullet list of the behaviour you exercised.",
                        },
                        "evidence": {
                            "type": "string",
                            "description": (
                                "Markdown numbered list of commands run and their real output. "
                                "Quote actual output — never describe it from memory."
                            ),
                        },
                        "edge_cases": {
                            "type": "string",
                            "description": "Markdown bullet list of edge cases probed, if any.",
                        },
                        "not_tested": {
                            "type": "string",
                            "description": "What you could not test and why, if anything.",
                        },
                    },
                    "required": ["status", "changes_tested", "evidence"],
                },
            },
        },
    ]


def run_bash(command, workspace, timeout):
    # Collect output in a temp file rather than a pipe: a backgrounded server inherits
    # the child's stdout, and draining a pipe to EOF would block until that server exits.
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as sink:
        try:
            completed = subprocess.run(
                ["bash", "-c", command],
                cwd=workspace,
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
            header = "exit code: %d" % completed.returncode
        except subprocess.TimeoutExpired:
            header = "command timed out after %ds (background long-running servers with &)" % timeout
        sink.seek(0)
        output = sink.read()

    if len(output) > MAX_OUTPUT_CHARS:
        half = MAX_OUTPUT_CHARS // 2
        output = "%s\n\n[... %d chars truncated ...]\n\n%s" % (
            output[:half],
            len(output) - MAX_OUTPUT_CHARS,
            output[-half:],
        )
    return "%s\n%s" % (header, output or "(no output)")


def render_report(arguments):
    """Turn submit_report arguments into the Markdown comment body."""
    status = str(arguments.get("status", "")).upper()
    if status not in VALID_STATUSES:
        status = "PARTIAL"

    sections = ["## QA Report", "", "**Status: %s**" % status]
    for heading, key in (
        ("Changes Tested", "changes_tested"),
        ("Evidence", "evidence"),
        ("Edge Cases", "edge_cases"),
        ("Not Tested", "not_tested"),
    ):
        value = (arguments.get(key) or "").strip()
        if value:
            sections += ["", "### %s" % heading, "", value]
    return status, "\n".join(sections)


def parse_arguments(raw):
    """Tool arguments arrive as a JSON string; some providers send an object."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("arguments are not valid JSON: %s" % error)
    if not isinstance(parsed, dict):
        raise ValueError("arguments must be a JSON object")
    return parsed
