"""Configuration for the QA agent, resolved from environment variables."""

import os
from dataclasses import dataclass, field

# Every provider below speaks the OpenAI-compatible `/chat/completions` API.
# `base_url` is the prefix the endpoint is appended to.
PROVIDERS = {
    "zai": {
        "base_url": "https://api.z.ai/api/coding/paas/v4",
        "model": "glm-5.3",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-pro",
    },
}

VALID_STATUSES = ("PASS", "FAIL", "PARTIAL")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    api_key: str
    base_url: str
    model: str
    provider: str
    github_token: str
    repo: str
    pr_number: int
    workspace: str
    max_turns: int
    command_timeout: int
    post_comment: bool
    setup_command: str
    effort: str
    request_timeout: int = 600
    time_budget: int = 1500
    max_context_chars: int = 240000
    artifacts_dir: str = ""
    run_url: str = ""
    github_api_url: str = "https://api.github.com"
    fail_on: tuple = field(default_factory=lambda: ("FAIL",))


def _env(name, default=""):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _int(name, default):
    raw = _env(name, default)
    try:
        return int(float(raw))
    except ValueError:
        raise ConfigError("%s must be a number, got %r" % (name, raw))


def _resolve_endpoint(provider, base_url, model):
    if provider == "custom":
        if not base_url:
            raise ConfigError("provider 'custom' requires base_url to be set")
    elif provider in PROVIDERS:
        base_url = base_url or PROVIDERS[provider]["base_url"]
        model = model or PROVIDERS[provider]["model"]
    else:
        known = ", ".join(sorted(PROVIDERS) + ["custom"])
        raise ConfigError("unknown provider %r (expected one of: %s)" % (provider, known))

    if not model:
        raise ConfigError("model must be set for provider %r" % provider)
    return base_url.rstrip("/"), model


def parse_fail_on(raw):
    """Which verdicts should turn the check red.

    `none` keeps the job green whatever the verdict, which is how you roll the
    action out to a repository before you trust it to gate merges.
    """
    cleaned = (raw or "").strip().lower()
    if cleaned in ("", "none", "never"):
        return ()
    statuses = []
    for part in cleaned.replace(",", " ").split():
        status = part.upper()
        if status not in VALID_STATUSES:
            raise ConfigError(
                "fail_on must list %s, or be 'none' — got %r"
                % (", ".join(VALID_STATUSES), part)
            )
        statuses.append(status)
    return tuple(statuses)


def from_env():
    provider = _env("QA_PROVIDER", "zai").lower()
    base_url, model = _resolve_endpoint(provider, _env("QA_BASE_URL"), _env("QA_MODEL"))

    api_key = _env("QA_API_KEY")
    if not api_key:
        raise ConfigError("QA_API_KEY is empty — set the LLM_API_KEY secret")

    repo = _env("QA_REPO")
    if not repo:
        raise ConfigError("QA_REPO is empty (expected 'owner/name')")

    pr_number = _env("QA_PR_NUMBER")
    if not pr_number.isdigit():
        raise ConfigError(
            "QA_PR_NUMBER is not a number: %r — set the `pr_number` input when the "
            "workflow is not triggered by a pull_request event" % pr_number
        )

    return Config(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=provider,
        github_token=_env("QA_GITHUB_TOKEN"),
        repo=repo,
        pr_number=int(pr_number),
        workspace=_env("QA_WORKSPACE", os.getcwd()),
        max_turns=_int("QA_MAX_TURNS", "40"),
        command_timeout=_int("QA_COMMAND_TIMEOUT", "300"),
        post_comment=_env("QA_POST_COMMENT", "true").lower() == "true",
        setup_command=_env("QA_SETUP_COMMAND"),
        effort=_env("QA_EFFORT"),
        request_timeout=_int("QA_REQUEST_TIMEOUT", "600"),
        time_budget=_int("QA_TIME_BUDGET", "1500"),
        max_context_chars=_int("QA_MAX_CONTEXT_CHARS", "240000"),
        artifacts_dir=_env("QA_ARTIFACTS_DIR"),
        run_url=_env("QA_RUN_URL"),
        github_api_url=_env("QA_GITHUB_API_URL", "https://api.github.com").rstrip("/"),
        fail_on=parse_fail_on(_env("QA_FAIL_ON", "FAIL")),
    )
