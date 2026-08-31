"""Tools the QA agent can call: run a shell command, and submit the report."""

import json
import os
import signal
import subprocess
import tempfile

MAX_OUTPUT_CHARS = 20000
VALID_STATUSES = ("PASS", "FAIL", "PARTIAL")

# The agent's instructions come partly from the pull request itself, so the
# commands it runs are only as trustworthy as the branch under test. Nothing the
# action introduces — and none of the runner's privileged tokens — is exposed to
# them. Repository-supplied variables are left alone: a project may legitimately
# need them to boot.
SECRET_PREFIXES = ("QA_", "INPUT_")
SECRET_NAMES = frozenset(
    {
        "LLM_API_KEY",
        "GITHUB_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        # The runner's env files are commands the workflow executes later, not
        # data. A line appended to GITHUB_ENV or GITHUB_PATH is applied to every
        # subsequent step in the calling job, so leaving them writable would let
        # the branch under test set variables — or prepend a directory to PATH —
        # for steps that run after QA. GITHUB_OUTPUT would let it dictate the
        # verdict, and GITHUB_STEP_SUMMARY the report the reviewer reads. The
        # agent has its own routes to all four; its commands need none of them.
        # Unsetting rather than redirecting is deliberate: tools that write
        # annotations check whether these are set and skip when they are not.
        "GITHUB_ENV",
        "GITHUB_PATH",
        "GITHUB_OUTPUT",
        "GITHUB_STEP_SUMMARY",
    }
)
# Scrubbed with everything else, then handed back: the agent needs somewhere to
# put screenshots and it carries no secret.
PASS_THROUGH = ("QA_ARTIFACTS_DIR",)

# Time a killed process group gets to exit before it is killed outright.
GRACE_SECONDS = 3


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


def child_environment(environ=None):
    """The environment the agent's commands run in, with our secrets removed."""
    source = os.environ if environ is None else environ
    safe = {
        name: value
        for name, value in source.items()
        if name not in SECRET_NAMES and not name.startswith(SECRET_PREFIXES)
    }
    for name in PASS_THROUGH:
        if source.get(name):
            safe[name] = source[name]
    return safe


def run_bash(command, workspace, timeout, environ=None):
    # Collect output in a temp file rather than a pipe: a backgrounded server inherits
    # the child's stdout, and draining a pipe to EOF would block until that server exits.
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as sink:
        process = subprocess.Popen(
            ["bash", "-c", command],
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
            env=child_environment(environ),
            # Its own process group, so a command that hangs can be cleaned up
            # along with everything it spawned instead of leaving the runner with
            # orphans holding ports open.
            start_new_session=os.name == "posix",
        )
        try:
            returncode = process.wait(timeout=timeout)
            header = "exit code: %d" % returncode
        except subprocess.TimeoutExpired:
            _terminate(process)
            header = (
                "command timed out after %ds and was killed "
                "(background long-running servers with &)" % timeout
            )
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


def _terminate(process):
    """Stop a timed-out command and anything it started."""
    for send, wait in ((signal.SIGTERM, GRACE_SECONDS), (signal.SIGKILL, GRACE_SECONDS)):
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), send)
            else:
                process.kill()
        except (ProcessLookupError, PermissionError, OSError):
            return
        try:
            process.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def render_report(arguments, footer=""):
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
    if footer.strip():
        sections += ["", footer.strip()]
    return status, "\n".join(sections)


def render_artifacts(artifacts_dir, run_url):
    """List whatever the agent saved, so screenshots reach the reviewer.

    A PR comment cannot carry an image the agent produced, so the files are
    uploaded as a workflow artifact and the report points at them.
    """
    names = collect_artifacts(artifacts_dir)
    if not names:
        return ""
    lines = ["### Artifacts", ""]
    lines += ["- `%s`" % name for name in names]
    if run_url:
        lines += ["", "Download them from the [workflow run](%s)." % run_url]
    else:
        lines += ["", "Attached to this workflow run."]
    return "\n".join(lines)


def collect_artifacts(artifacts_dir, limit=50):
    """Relative paths of the files the agent left behind, sorted and capped."""
    if not artifacts_dir or not os.path.isdir(artifacts_dir):
        return []
    names = []
    for root, _, files in os.walk(artifacts_dir):
        for name in files:
            names.append(os.path.relpath(os.path.join(root, name), artifacts_dir))
    names.sort()
    if len(names) > limit:
        names = names[:limit] + ["… and %d more" % (len(names) - limit)]
    return names


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
