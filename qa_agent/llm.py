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

MAX_BACKOFF = 60
# Floor for a request made after the run's time budget is spent: the wind-down
# turns still have to reach the model to get a report out of it.
WIND_DOWN_TIMEOUT = 60


class LLMError(Exception):
    """A request the client could not complete. Always reported, never silent.

    The message is written to be published: it ends up in the QA report, which
    is a public pull request comment. `detail` is the raw response body, which
    is for the job log only — a proxy's error page can carry internal hostnames
    and paths, and an auth failure can echo request identifiers.
    """

    def __init__(self, summary, detail=""):
        super().__init__(summary)
        self.detail = detail


class _MalformedBody(Exception):
    """A 200 that is not JSON — usually a proxy or gateway answering for the API.

    Internal: retried like any other transient failure, and surfaced as an
    LLMError once the attempts are spent, so it can never escape as a traceback.
    """

    def __init__(self, body):
        super().__init__("non-JSON body")
        self.body = body


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
        raw = response.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        # An HTML error page returned with a 200, a truncated body, an empty
        # response. json.JSONDecodeError is a ValueError, so it matches neither
        # of the urllib handlers in complete() and would leave as a traceback.
        raise _MalformedBody(raw)


def complete(config, messages, tools, timeout=None, attempts=5, deadline=None):
    """Send one chat-completion request and return a Completion.

    `deadline` is the run's time budget as a monotonic timestamp. Requests are
    clamped to what is left of it and retries stop once it passes, so a hanging
    or flapping endpoint cannot outlive the budget the report has to be written
    inside of.
    """
    url = "%s/chat/completions" % config.base_url
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
        request_timeout = timeout or _attempt_timeout(config, deadline)
        try:
            body = _post(url, payload, config.api_key, request_timeout)
            break
        # HTTPError is a subclass of URLError, so it has to be caught first.
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:2000]
            last_error = _http_error(config, url, error.code, detail)
            if not _worth_retrying(error.code, detail):
                raise last_error
            delay = _backoff(attempt, error.headers.get("Retry-After"))
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = LLMError(
                "the model endpoint (provider: %s) could not be reached: %s"
                % (config.provider, type(error).__name__),
                detail="%s: %s" % (url, error),
            )
            delay = _backoff(attempt, None)
        except _MalformedBody as error:
            last_error = LLMError(
                "the model endpoint (provider: %s) returned a success status with a "
                "body that is not JSON (%d bytes) — see the job log for it"
                % (config.provider, len(error.body)),
                detail="%s returned a non-JSON body:\n%s"
                % (url, (error.body.strip() or "(empty body)")[:2000]),
            )
            delay = _backoff(attempt, None)

        if attempt >= attempts - 1 or _spent(deadline):
            break
        time.sleep(delay)

    if body is None:
        raise last_error

    choices = body.get("choices") or []
    if not choices:
        raise LLMError(
            "the model endpoint (provider: %s) returned no choices" % config.provider,
            detail=json.dumps(body)[:2000],
        )

    choice = choices[0]
    usage = body.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    return Completion(
        message=normalize(choice.get("message") or {}),
        finish_reason=choice.get("finish_reason") or "",
        prompt_tokens=_count(usage.get("prompt_tokens")),
        cached_tokens=_count(details.get("cached_tokens")),
    )


def _count(value):
    """Token counts are advisory: a provider sending nonsense must not crash us."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _spent(deadline):
    return deadline is not None and time.monotonic() >= deadline


def _attempt_timeout(config, deadline):
    """How long one request may take, given what is left of the time budget."""
    if deadline is None:
        return config.request_timeout
    remaining = int(deadline - time.monotonic())
    if remaining <= 0:
        # Past the budget, so this is a wind-down turn asking for the report.
        return WIND_DOWN_TIMEOUT
    return max(WIND_DOWN_TIMEOUT, min(config.request_timeout, remaining))


def _provider_message(detail):
    """The human-readable message from a structured error body, if there is one.

    Providers write these for developers to read, so this is the part of a
    failure that is safe to put in a public comment. The rest of the body is not.
    """
    try:
        error = json.loads(detail).get("error") or {}
    except (ValueError, AttributeError):
        return ""
    message = error.get("message")
    return message.strip() if isinstance(message, str) else ""


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
            return min(max(0.0, float(retry_after)), MAX_BACKOFF)
        except ValueError:
            pass
    # Jittered, so parallel QA jobs on the same key do not retry in lockstep.
    return min(2 ** attempt + random.uniform(0, 1), MAX_BACKOFF)


def _http_error(config, url, status, detail):
    """Split a failed response into a publishable summary and a private detail."""
    summary = "the model endpoint (provider: %s) returned HTTP %s" % (config.provider, status)
    message = _provider_message(detail)
    if message:
        summary += ": %s" % message[:400]
    summary += _advice(status, detail)
    return LLMError(summary, detail="%s -> HTTP %s\n%s" % (url, status, detail))


def _advice(status, detail):
    business = _business_code(detail)
    if business == "1261" or (status == 400 and "too long" in detail.lower()):
        return (
            "\nThe prompt is too long for this model — lower `max_turns`, or set "
            "`context_tokens` so the agent compacts before it gets here."
        )
    if status in (401, 403):
        return "\nCheck the `api_key` input and that the key is valid for this provider."
    if status == 429 and business and business not in TRANSIENT_CODES:
        return (
            "\nThis is a quota or subscription limit rather than a rate limit, so it "
            "was not retried. The provider's message above says when it resets."
        )
    return ""


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
