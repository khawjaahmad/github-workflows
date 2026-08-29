"""Client for OpenAI-compatible /chat/completions endpoints.

Works against any provider that implements the OpenAI v1 wire format: z.ai's
coding plan, Google's Gemini compatibility layer, OpenAI itself, or a
self-hosted gateway.

Two provider behaviours shape this module, both from z.ai's documentation, and
both harmless on providers that do not do them:

- Reasoning models return `reasoning_content` alongside the answer, and z.ai's
  Preserved Thinking — on by default for the coding-plan endpoint we target —
  requires it to be sent back "full, unmodified, and correctly ordered".
  https://docs.z.ai/guides/capabilities/thinking-mode
- A context overflow arrives as a *successful* response carrying
  `finish_reason: model_context_window_exceeded`, not as an HTTP error.
  https://docs.z.ai/api-reference/llm/chat-completion
"""

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}

# z.ai returns an HTTP status plus a business code, and maps a lot of permanent
# conditions onto 429 — an exhausted plan, an expired subscription, a model the
# subscription does not include. Retrying those burns the job's time budget and
# ends in a vaguer message than the one the provider already gave us, so only
# the genuinely transient codes are retried.
# https://docs.z.ai/api-reference/api-code
TRANSIENT_CODES = {
    "1200",  # API call error
    "1230",  # API call process error
    "1234",  # network error, please try again later
    "1302",  # rate limit reached for requests
    "1305",  # service temporarily overloaded
}

# finish_reason values across providers. z.ai documents stop, tool_calls, length,
# sensitive, model_context_window_exceeded and network_error; OpenAI documents
# stop, length, tool_calls, content_filter and function_call.
OVERFLOW_REASONS = {"model_context_window_exceeded"}
TRUNCATED_REASONS = {"length"}
BLOCKED_REASONS = {"sensitive", "content_filter"}
RETRYABLE_REASONS = {"network_error"}

MAX_BACKOFF = 60


class LLMError(Exception):
    """A request the client could not complete. Always reported, never silent."""


@dataclass
class Completion:
    """One assistant turn, with the metadata the agent loop needs to react."""

    message: dict
    finish_reason: str = ""
    prompt_tokens: int = 0
    cached_tokens: int = 0

    @property
    def overflowed(self):
        return self.finish_reason in OVERFLOW_REASONS

    @property
    def truncated(self):
        return self.finish_reason in TRUNCATED_REASONS

    @property
    def blocked(self):
        return self.finish_reason in BLOCKED_REASONS


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
    """Send one chat-completion request and return a Completion."""
    url = "%s/chat/completions" % config.base_url
    timeout = timeout or config.request_timeout
    payload = {
        "model": config.model,
        "messages": messages,
        "tools": tools,
        # z.ai supports auto only; OpenAI and Gemini default to it.
        "tool_choice": "auto",
    }
    # Reasoning effort is passed through verbatim. Each provider has its own
    # vocabulary and its own set of valid levels, and they change with each model
    # release — validating here would only add a way to be wrong. Omitted
    # entirely when unset, so the model keeps its own default.
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
            if not _worth_retrying(error.code, detail):
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

    choice = choices[0]
    usage = body.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    return Completion(
        message=normalize(choice.get("message") or {}),
        finish_reason=choice.get("finish_reason") or "",
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        cached_tokens=int(details.get("cached_tokens") or 0),
    )


def _business_code(detail):
    """The provider's own error code, when the body carries one."""
    try:
        error = json.loads(detail).get("error") or {}
    except (ValueError, AttributeError):
        return ""
    return str(error.get("code") or "")


def _worth_retrying(status, detail):
    if status not in RETRY_STATUSES:
        return False
    code = _business_code(detail)
    # An unrecognised body is retried on status alone; a known permanent code
    # (an exhausted quota, an expired plan) is not.
    return not code or code in TRANSIENT_CODES or status >= 500


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
    business = _business_code(detail)
    if business == "1261" or (code == 400 and "too long" in detail.lower()):
        message += "\nThe prompt is too long for this model — lower `max_turns`, or set "
        message += "`context_tokens` so the agent compacts before it gets here."
    elif code in (401, 403):
        message += "\nCheck the `api_key` input and that the key is valid for this provider."
    elif code == 429 and business and business not in TRANSIENT_CODES:
        message += "\nThis is a quota or subscription limit rather than a rate limit, so it "
        message += "was not retried. The provider's message above says when it resets."
    return message


def normalize(message):
    """Coerce a provider response into the message shape we send back.

    Providers differ on nullable fields: some omit `content` entirely when
    returning tool calls, others send null. Both break strict request
    validation on the next turn, so normalize once here.

    `reasoning_content` is carried through byte-for-byte. Rewriting or dropping
    it breaks z.ai's Preserved Thinking, which its docs say may "degrade
    performance or prevent the feature from taking effect", and costs the cache
    hits that make a long agent run affordable.
    """
    normalized = {"role": "assistant", "content": message.get("content") or ""}

    reasoning = message.get("reasoning_content")
    if reasoning:
        normalized["reasoning_content"] = reasoning

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
