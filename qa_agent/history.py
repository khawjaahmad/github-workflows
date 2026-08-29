"""Keeps the conversation inside the model's context window.

A QA run is tool-heavy: forty turns of command output, resent in full on every
request, will outgrow any context window. Compaction shrinks the oldest tool
output first, which is also the output the model is least likely to still need.

Messages are only ever *shrunk*, never dropped: every `tool_calls` id must keep
its matching `tool` reply, or the next request is rejected as malformed.
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

    for limit, roles in (
        # Old tool output first: bulky, and superseded by whatever the model
        # concluded from it.
        (older, ("tool",)),
        # Then the model's own older prose. Tool calls themselves are left intact
        # so the transcript stays structurally valid.
        (older, ("assistant",)),
        # Still too big: the recent turns go as well, all but the last exchange,
        # which is what the model is answering right now.
        (max(0, len(compacted) - 2), ("tool", "assistant")),
    ):
        for index in range(limit):
            if _shrink(compacted[index], roles) and size(compacted) <= max_chars:
                return compacted

    # Best effort. What is left — the system prompt, the pull request context and
    # the last exchange — is the part worth keeping if anything is.
    return compacted


def _shrink(message, roles):
    """Replace one message's content with a note about its former size."""
    if message.get("role") not in roles:
        return False
    content = message.get("content") or ""
    if not content or content.startswith("[earlier output elided"):
        return False
    message["content"] = ELIDED % len(content)
    return True
