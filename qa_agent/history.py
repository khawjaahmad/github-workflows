"""Keeps the conversation inside the model's context window.

A QA run is tool-heavy: forty turns of command output, resent in full on every
request, will outgrow any context window.

Order matters here. Reasoning content is the last thing to go, because z.ai's
Preserved Thinking wants it returned "full, unmodified, and correctly ordered"
and gives up performance and cache hits when it is not — so it is only dropped
when the alternative is a request that cannot succeed at all. When it does go,
it goes whole: a rewritten block is worse than an absent one.
https://docs.z.ai/guides/capabilities/thinking-mode

Messages themselves are only ever shrunk, never dropped: every `tool_calls` id
must keep its matching `tool` reply, or the next request is rejected.
"""

ELIDED = "[earlier output elided to fit the context window; was %d characters]"

# Recent turns are what the model is actually reasoning about, so they are left
# alone until there is nothing older left to shrink.
KEEP_RECENT = 8


def size(messages):
    """Approximate the request size in characters."""
    total = 0
    for message in messages:
        total += len(message.get("content") or "")
        total += len(message.get("reasoning_content") or "")
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            total += len(function.get("name") or "") + len(function.get("arguments") or "")
    return total


def compact(messages, max_chars, keep_recent=KEEP_RECENT):
    """Return a copy of `messages` that fits in `max_chars`, as far as possible.

    A budget of zero or less disables compaction.
    """
    if max_chars <= 0 or size(messages) <= max_chars:
        return messages

    compacted = [dict(message) for message in messages]
    older = max(0, len(compacted) - keep_recent)

    for limit, step in (
        # Old tool output first: bulky, and superseded by whatever the model
        # concluded from it.
        (older, _elide_tool),
        # Then the model's own older prose. Tool calls themselves are left intact
        # so the transcript stays structurally valid.
        (older, _elide_assistant),
        # Then old reasoning, dropped whole rather than rewritten.
        (older, _drop_reasoning),
        # Still too big: the recent turns go as well, all but the last exchange,
        # which is what the model is answering right now.
        (max(0, len(compacted) - 2), _elide_tool),
        (max(0, len(compacted) - 2), _elide_assistant),
        (max(0, len(compacted) - 2), _drop_reasoning),
    ):
        for index in range(limit):
            if step(compacted[index]) and size(compacted) <= max_chars:
                return compacted

    # Best effort. What is left — the system prompt, the pull request context and
    # the last exchange — is the part worth keeping if anything is.
    return compacted


def _elide_tool(message):
    return _elide(message, "tool")


def _elide_assistant(message):
    return _elide(message, "assistant")


def _elide(message, role):
    """Replace one message's content with a note about its former size."""
    if message.get("role") != role:
        return False
    content = message.get("content") or ""
    if not content or content.startswith("[earlier output elided"):
        return False
    message["content"] = ELIDED % len(content)
    return True


def _drop_reasoning(message):
    """Remove a reasoning block entirely — never edit one in place."""
    if not message.get("reasoning_content"):
        return False
    del message["reasoning_content"]
    return True
