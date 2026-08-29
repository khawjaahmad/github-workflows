"""The agent loop: talk to the model, run its tool calls, collect the report."""

import sys

from . import llm, prompt, tools

GAVE_UP_REPORT = {
    "status": "PARTIAL",
    "changes_tested": "The QA agent ran out of turns before finishing.",
    "evidence": (
        "The agent reached its %d-turn budget without submitting a report. Raise `max_turns` "
        "or narrow the pull request."
    ),
}


def log(message):
    print(message, flush=True)
    sys.stdout.flush()


def run(config, pull_request, diff):
    """Drive the agent until it submits a report or runs out of turns.

    Returns (status, markdown_body).
    """
    tool_definitions = tools.definitions(config.command_timeout)
    messages = [
        {"role": "system", "content": prompt.SYSTEM_PROMPT},
        {
            "role": "user",
            "content": prompt.initial_message(pull_request, diff, config.setup_command),
        },
    ]

    for turn in range(1, config.max_turns + 1):
        message = llm.complete(config, messages, tool_definitions)
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
                status, body = tools.render_report(arguments)
                log("[turn %d] submit_report -> %s" % (turn, status))
                return status, body

            if name == "bash":
                command = arguments.get("command", "")
                timeout = int(arguments.get("timeout") or config.command_timeout)
                log("[turn %d] bash: %s" % (turn, command))
                result = tools.run_bash(command, config.workspace, timeout)
                messages.append(_tool_result(call, result))
                continue

            messages.append(_tool_result(call, "error: unknown tool %r" % name))

    log("reached max turns (%d) without a report" % config.max_turns)
    gave_up = dict(GAVE_UP_REPORT)
    gave_up["evidence"] = gave_up["evidence"] % config.max_turns
    return tools.render_report(gave_up)


def _tool_result(call, content):
    return {"role": "tool", "tool_call_id": call["id"], "content": content}
