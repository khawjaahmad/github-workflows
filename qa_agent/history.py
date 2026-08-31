"""Keeps the conversation inside the model's context window."""

ELIDED = "[earlier output elided to fit the context window; was %d characters]"
TRUNCATED = "\n[... %d characters truncated to fit the context window]"

MIN_TOOL_CHARS = 400

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
    """Return a copy of `messages` fitting `max_chars`; a budget <= 0 disables it."""
    if max_chars <= 0 or size(messages) <= max_chars:
        return messages

    compacted = [dict(message) for message in messages]
    older = max(0, len(compacted) - keep_recent)

    for limit, step in (
        (older, _elide_tool),
        (older, _elide_assistant),
        (older, _drop_reasoning),
        (max(0, len(compacted) - 2), _elide_tool),
        (max(0, len(compacted) - 2), _elide_assistant),
        (max(0, len(compacted) - 2), _drop_reasoning),
    ):
        for index in range(limit):
            if step(compacted[index]) and size(compacted) <= max_chars:
                return compacted

    for index in range(len(compacted)):
        if _truncate_tool(compacted[index], size(compacted) - max_chars):
            if size(compacted) <= max_chars:
                return compacted

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


def _truncate_tool(message, over):
    """Cut `over` characters off the end of a tool result, keeping the start."""
    if message.get("role") != "tool" or over <= 0:
        return False
    content = message.get("content") or ""
    keep = max(MIN_TOOL_CHARS, len(content) - over - len(TRUNCATED % over))
    if keep >= len(content):
        return False
    message["content"] = content[:keep] + TRUNCATED % (len(content) - keep)
    return True


def _drop_reasoning(message):
    """Remove a reasoning block entirely — never edit one in place."""
    if not message.get("reasoning_content"):
        return False
    del message["reasoning_content"]
    return True
