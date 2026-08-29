"""Configuration for the QA agent, resolved from environment variables."""

import os
from dataclasses import dataclass

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


def _env(name, default=""):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


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
        raise ConfigError("QA_PR_NUMBER is not a number: %r" % pr_number)

    return Config(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=provider,
        github_token=_env("QA_GITHUB_TOKEN"),
        repo=repo,
        pr_number=int(pr_number),
        workspace=_env("QA_WORKSPACE", os.getcwd()),
        max_turns=int(_env("QA_MAX_TURNS", "40")),
        command_timeout=int(_env("QA_COMMAND_TIMEOUT", "300")),
        post_comment=_env("QA_POST_COMMENT", "true").lower() == "true",
        setup_command=_env("QA_SETUP_COMMAND"),
        effort=_env("QA_EFFORT"),
    )
