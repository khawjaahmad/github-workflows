"""Minimal client for OpenAI-compatible /chat/completions endpoints.

Works against any provider that implements the OpenAI v1 wire format:
z.ai's coding plan, Google's Gemini compatibility layer, OpenAI itself, or a
self-hosted gateway.
"""

import json
import random
import time
import urllib.error
import urllib.request

RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_BACKOFF = 60


class LLMError(Exception):
    """A request the client could not complete. Always reported, never silent."""


def _post(url, payload, api_key, timeout):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def complete(config, messages, tools, timeout=None, attempts=5):
    """Send one chat-completion request and return the assistant message."""
    url = "%s/chat/completions" % config.base_url
    timeout = timeout or config.request_timeout
    payload = {
        "model": config.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
    # Reasoning effort is passed through verbatim: each provider has its own
    # vocabulary (z.ai low/high/max, OpenAI low/medium/high/xhigh), and a new
    # level should not need a change here. Omitted entirely when unset, so the
    # model keeps its own default.
    if config.effort:
        payload["reasoning_effort"] = config.effort

    body = None
    last_error = None
    for attempt in range(attempts):
        try:
            body = _post(url, payload, config.api_key, timeout)
            break
        # HTTPError is a subclass of URLError, so it has to be caught first.
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:2000]
            last_error = LLMError(_describe(error.code, url, detail))
            if error.code not in RETRY_STATUSES:
                raise last_error
            delay = _backoff(attempt, error.headers.get("Retry-After"))
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = LLMError("request to %s failed: %s" % (url, error))
            delay = _backoff(attempt, None)
        if attempt < attempts - 1:
            time.sleep(delay)

    if body is None:
        raise last_error

    choices = body.get("choices") or []
    if not choices:
        raise LLMError("no choices in response: %s" % json.dumps(body)[:2000])
    return normalize(choices[0].get("message") or {})


def _backoff(attempt, retry_after):
    """Seconds to wait — the server's own advice when it gave any."""
    if retry_after:
        try:
            return min(float(retry_after), MAX_BACKOFF)
        except ValueError:
            pass
    # Jittered, so parallel QA jobs on the same key do not retry in lockstep.
    return min(2 ** attempt + random.uniform(0, 1), MAX_BACKOFF)


def _describe(code, url, detail):
    message = "HTTP %s from %s: %s" % (code, url, detail)
    lowered = detail.lower()
    if code == 400 and ("context" in lowered or "too long" in lowered or "max_tokens" in lowered):
        message += (
            "\nThe conversation is too long for this model — lower `max_context_chars` "
            "or `max_turns`."
        )
    elif code in (401, 403):
        message += "\nCheck the `api_key` input and that the key is valid for this provider."
    return message


def normalize(message):
    """Coerce a provider response into the message shape we send back.

    Providers differ on nullable fields: some omit `content` entirely when
    returning tool calls, others send null. Both break strict request
    validation on the next turn, so normalize once here.
    """
    normalized = {"role": "assistant", "content": message.get("content") or ""}
    tool_calls = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        tool_calls.append(
            {
                "id": call.get("id") or "call_%d" % index,
                "type": "function",
                "function": {
                    "name": function.get("name") or "",
                    "arguments": function.get("arguments") or "{}",
                },
            }
        )
    if tool_calls:
        normalized["tool_calls"] = tool_calls
    return normalized
