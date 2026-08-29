"""The agent loop: talk to the model, run its tool calls, collect the report."""

import signal
import sys
import time

from . import history, llm, prompt, tools

# Model turns allowed after the agent is told to wrap up, so it can turn what it
# already has into a report instead of being cut off mid-run.
GRACE_TURNS = 2
# How much of the command trail to quote when the agent never reported itself.
TRAIL_LIMIT = 20

# Characters per token, used only to turn a token budget into the character
# budget compaction works in. Deliberately pessimistic: over-compacting costs
# some context, under-compacting costs the whole request.
CHARS_PER_TOKEN = 3

# Signals that mean "stop now": the runner sends SIGTERM before it kills a job
# that has run out of time, and an agent poking at process management can signal
# itself by accident.
TERMINATION_SIGNALS = {"SIGTERM": None, "SIGINT": None}


class Interrupted(Exception):
    """The run was signalled to stop before the agent had reported."""


def log(message):
    print(message, flush=True)
    sys.stdout.flush()


def run(config, pull_request, diff):
    """Drive the agent until it submits a report or runs out of budget.

    Always returns (status, markdown_body): a run that fails, stalls, times out
    or is killed still produces a report, because a pull request with no QA
    comment and a red check tells the author nothing.
    """
    trail = []
    restore = _trap_termination()
    try:
        return _loop(config, pull_request, diff, trail)
    except Interrupted as error:
        log(str(error))
        return _fallback(config, str(error), trail)
    finally:
        restore()


def _trap_termination():
    """Turn a termination signal into a report instead of a dead job.

    Returns a callable that puts the previous handlers back.
    """

    def handle(number, _frame):
        raise Interrupted(
            "the run was stopped by %s — a job timeout, or a command that killed "
            "this process" % _signal_name(number)
        )

    previous = {}
    for name in TERMINATION_SIGNALS:
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            previous[number] = signal.signal(number, handle)
        except (ValueError, OSError):
            # Not the main thread, or the platform does not allow it.
            continue

    def restore():
        for number, handler in previous.items():
            try:
                signal.signal(number, handler)
            except (ValueError, OSError):
                pass

    return restore


def _signal_name(number):
    try:
        return signal.Signals(number).name
    except ValueError:
        return "signal %s" % number


def _loop(config, pull_request, diff, trail):
    tool_definitions = tools.definitions(config.command_timeout)
    messages = [
        {"role": "system", "content": prompt.SYSTEM_PROMPT},
        {"role": "user", "content": prompt.initial_message(pull_request, diff, config)},
    ]

    deadline = time.monotonic() + config.time_budget if config.time_budget > 0 else None
    winding_down = None
    grace = GRACE_TURNS
    turn = 0
    prompt_tokens = 0
    overflows = 0

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

        messages = history.compact(messages, _budget(config, prompt_tokens))
        try:
            completion = llm.complete(config, messages, tool_definitions)
        except llm.LLMError as error:
            # The endpoint is gone or the request is unacceptable. Retrying is
            # llm.complete's job and it already did; report what we have.
            log("model endpoint failed: %s" % error)
            return _fallback(config, "the model endpoint failed: %s" % error, trail)

        prompt_tokens = completion.prompt_tokens or prompt_tokens
        message = completion.message

        # A context overflow arrives as a successful response, not an error. Left
        # unhandled it looks like a turn that simply called no tools, and the loop
        # would nudge the model until the budget ran out.
        if completion.overflowed:
            log("[turn %d] the model reports the context window was exceeded" % turn)
            if overflows >= 1:
                return _fallback(
                    config, "the context window was exceeded and compacting did not "
                    "recover it", trail
                )
            overflows += 1
            messages = history.compact(messages, _shrink(messages))
            continue

        if completion.blocked:
            return _fallback(
                config,
                "the provider stopped the response (finish_reason: %s)"
                % completion.finish_reason,
                trail,
            )

        messages.append(message)

        if message["content"]:
            log("[turn %d] %s" % (turn, message["content"]))

        if completion.truncated:
            log("[turn %d] the response was truncated at the output limit" % turn)

        calls = message.get("tool_calls")
        if not calls:
            # Nothing to run. A truncated turn was cut off before it got to its
            # tool call, which is worth naming; otherwise the model has simply
            # stopped, and a nudge beats ending with no verdict. Either way the
            # reply must come after the tool results, never instead of them —
            # an assistant turn with unanswered tool_call ids is rejected.
            messages.append({"role": "user", "content": _nudge(completion)})
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


TRUNCATED_NUDGE = (
    "Your last response was cut off at the output limit before you called a tool. "
    "Keep replies short: make one tool call at a time, and save long prose for "
    "submit_report."
)
CONTINUE_NUDGE = "Continue testing, or call submit_report if you are done."


def _nudge(completion):
    return TRUNCATED_NUDGE if completion.truncated else CONTINUE_NUDGE


def _budget(config, prompt_tokens):
    """The character budget for compaction, or 0 while there is no reason to.

    The provider reports how many prompt tokens the last request actually used,
    so the only guess left is the model's window — and that is stated by the
    `context_tokens` input rather than assumed. Without it the agent does not
    compact speculatively; it waits for the provider to say the window was
    exceeded, and compacts then.
    """
    if config.context_tokens <= 0:
        return 0
    ceiling = int(config.context_tokens * config.context_headroom)
    if prompt_tokens and prompt_tokens < ceiling:
        return 0
    return ceiling * CHARS_PER_TOKEN


def _shrink(messages):
    """A budget that forces compaction to actually give something up."""
    return max(4000, history.size(messages) // 2)


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
