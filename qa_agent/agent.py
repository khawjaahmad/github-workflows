"""The agent loop: talk to the model, run its tool calls, collect the report."""

import sys
import time

from . import history, llm, prompt, tools

# Model turns allowed after the agent is told to wrap up, so it can turn what it
# already has into a report instead of being cut off mid-run.
GRACE_TURNS = 2
# How much of the command trail to quote when the agent never reported itself.
TRAIL_LIMIT = 20


def log(message):
    print(message, flush=True)
    sys.stdout.flush()


def run(config, pull_request, diff):
    """Drive the agent until it submits a report or runs out of budget.

    Always returns (status, markdown_body): a run that fails, stalls or times out
    still produces a report, because a pull request with no QA comment and a red
    check tells the author nothing.
    """
    tool_definitions = tools.definitions(config.command_timeout)
    messages = [
        {"role": "system", "content": prompt.SYSTEM_PROMPT},
        {"role": "user", "content": prompt.initial_message(pull_request, diff, config)},
    ]

    deadline = time.monotonic() + config.time_budget if config.time_budget > 0 else None
    trail = []
    winding_down = None
    grace = GRACE_TURNS
    turn = 0

    while True:
        turn += 1
        if winding_down is None:
            winding_down = _budget_spent(turn, config, deadline)
            if winding_down:
                log("winding down: %s" % winding_down)
                messages.append({"role": "user", "content": prompt.FINAL_CALL % winding_down})
        elif grace <= 0:
            log("no report after the wind-down turns")
            return _fallback(config, winding_down, trail)
        if winding_down:
            grace -= 1

        messages = history.compact(messages, config.max_context_chars)
        try:
            message = llm.complete(config, messages, tool_definitions)
        except llm.LLMError as error:
            # The endpoint is gone or the request is unacceptable. Retrying is
            # llm.complete's job and it already did; report what we have.
            log("model endpoint failed: %s" % error)
            return _fallback(config, "the model endpoint failed: %s" % error, trail)
        messages.append(message)

        if message["content"]:
            log("[turn %d] %s" % (turn, message["content"]))

        calls = message.get("tool_calls")
        if not calls:
            # A model that stops calling tools has nothing left to contribute; nudge
            # it once toward the report rather than ending without a verdict.
            messages.append(
                {
                    "role": "user",
                    "content": "Continue testing, or call submit_report if you are done.",
                }
            )
            continue

        for call in calls:
            name = call["function"]["name"]
            try:
                arguments = tools.parse_arguments(call["function"]["arguments"])
            except ValueError as error:
                messages.append(_tool_result(call, "error: %s" % error))
                continue

            if name == "submit_report":
                status, body = tools.render_report(arguments, _artifacts(config))
                log("[turn %d] submit_report -> %s" % (turn, status))
                return status, body

            if name == "bash":
                command = arguments.get("command", "")
                timeout = _timeout(arguments.get("timeout"), config, deadline)
                log("[turn %d] bash: %s" % (turn, command))
                result = tools.run_bash(command, config.workspace, timeout)
                trail.append((command, result.splitlines()[0] if result else ""))
                messages.append(_tool_result(call, result))
                continue

            messages.append(_tool_result(call, "error: unknown tool %r" % name))


def _budget_spent(turn, config, deadline):
    """Why the agent should wrap up now, or None while it still has room."""
    if turn > config.max_turns:
        return "the %d-turn budget is spent" % config.max_turns
    if deadline is not None and time.monotonic() >= deadline:
        return "the %ds time budget is spent" % config.time_budget
    return None


def _timeout(requested, config, deadline):
    """Never let one command run past the point where the report must be written."""
    timeout = int(requested or config.command_timeout)
    if deadline is not None:
        remaining = int(deadline - time.monotonic())
        if remaining < timeout:
            # A floor, so the last command still gets a chance to say something.
            timeout = max(10, remaining)
    return timeout


def _artifacts(config):
    return tools.render_artifacts(config.artifacts_dir, config.run_url)


def _fallback(config, reason, trail):
    """A report for a run that ended before the agent wrote its own."""
    evidence = ["The agent stopped before submitting a report: %s." % reason]
    if trail:
        evidence += ["", "Commands it had run, most recent last:", ""]
        evidence += [
            "%d. `%s` — %s" % (index, command, outcome)
            for index, (command, outcome) in enumerate(trail[-TRAIL_LIMIT:], 1)
        ]
    else:
        evidence += ["", "It had not run any commands."]

    return tools.render_report(
        {
            "status": "PARTIAL",
            "changes_tested": (
                "Nothing was confirmed — this report was generated on the agent's behalf."
            ),
            "evidence": "\n".join(evidence),
            "not_tested": (
                "Everything not listed above. Raise `max_turns` or `time_budget`, narrow the "
                "pull request, or check the workflow log for the underlying error."
            ),
        },
        _artifacts(config),
    )


def _tool_result(call, content):
    return {"role": "tool", "tool_call_id": call["id"], "content": content}
