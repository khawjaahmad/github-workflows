"""Minimal client for OpenAI-compatible /chat/completions endpoints.

Works against any provider that implements the OpenAI v1 wire format:
z.ai's coding plan, Google's Gemini compatibility layer, OpenAI itself, or a
self-hosted gateway.
"""

import json
import time
import urllib.error
import urllib.request

RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504}


class LLMError(Exception):
    pass


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


def complete(config, messages, tools, timeout=300, attempts=4):
    """Send one chat-completion request and return the assistant message."""
    url = "%s/chat/completions" % config.base_url
    payload = {
        "model": config.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }

    last_error = None
    for attempt in range(attempts):
        try:
            body = _post(url, payload, config.api_key, timeout)
            break
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:2000]
            last_error = LLMError("HTTP %s from %s: %s" % (error.code, url, detail))
            if error.code not in RETRY_STATUSES:
                raise last_error
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = LLMError("request to %s failed: %s" % (url, error))
        time.sleep(2 ** attempt)
    else:
        raise last_error

    choices = body.get("choices") or []
    if not choices:
        raise LLMError("no choices in response: %s" % json.dumps(body)[:2000])
    return normalize(choices[0].get("message") or {})


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
