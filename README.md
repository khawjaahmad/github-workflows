# qa-changes

Automated QA testing for pull requests: an agent that **runs the software** instead of
reading the diff. It sets up the repository, exercises the changed behaviour as a real user
would (CLI, HTTP, browser), and posts a structured QA report as a PR comment.

It is not a code reviewer and not a test runner — those jobs already have owners.
See [TASK.md](TASK.md) for the methodology in full.

## Quick start

1. Add your provider key as `LLM_API_KEY` under **Settings → Secrets and variables → Actions**.
2. Copy this workflow into `.github/workflows/qa-changes.yml`:

```yaml
name: QA Changes

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write

jobs:
  qa:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0 # so the agent can check out the base branch for before/after testing
      - uses: khawjaahmad/github-workflows@main
        with:
          api_key: ${{ secrets.LLM_API_KEY }}
          provider: zai
```

## Providers

The agent speaks the OpenAI-compatible `/v1` chat-completions wire format, so any endpoint
that implements it works. `provider` picks the defaults:

| `provider` | Base URL                                                 | Default `model`    |
| ---------- | -------------------------------------------------------- | ------------------ |
| `zai`      | `https://api.z.ai/api/coding/paas/v4`                    | `glm-4.6`          |
| `openai`   | `https://api.openai.com/v1`                              | `gpt-5`            |
| `gemini`   | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-pro`   |
| `custom`   | set `base_url` yourself                                   | set `model` yourself |

`model` and `base_url` override the defaults for any provider, so a newer model id needs no
change here:

```yaml
      - uses: khawjaahmad/github-workflows@main
        with:
          api_key: ${{ secrets.LLM_API_KEY }}
          provider: gemini
          model: gemini-2.5-flash
```

```yaml
      - uses: khawjaahmad/github-workflows@main
        with:
          api_key: ${{ secrets.LLM_API_KEY }}
          provider: custom
          base_url: https://gateway.internal/v1
          model: my-deployment
```

The provider must support OpenAI-style function calling — the agent drives everything through
two tools, `bash` and `submit_report`.

## Inputs

| Input             | Default          | Description                                                             |
| ----------------- | ---------------- | ----------------------------------------------------------------------- |
| `api_key`         | _(required)_     | Provider API key.                                                        |
| `provider`        | `zai`            | `zai`, `openai`, `gemini` or `custom`.                                   |
| `model`           | per provider     | Model id.                                                                |
| `base_url`        | per provider     | OpenAI-compatible base URL; required for `custom`.                       |
| `github_token`    | `github.token`   | Reads the PR and posts the report; needs `pull-requests: write`.          |
| `pr_number`       | from the event   | PR to QA. Required when not triggered by `pull_request`.                 |
| `setup_command`   | _(none)_         | Command the agent runs before it starts testing.                         |
| `max_turns`       | `40`             | Model turns before the agent gives up and reports `PARTIAL`.             |
| `command_timeout` | `300`            | Default per-command timeout, in seconds.                                 |
| `post_comment`    | `true`           | Set to `false` to log the report without commenting.                     |
| `python_version`  | `3.11`           | Python used to run the agent.                                            |

Output `status` is `PASS`, `FAIL` or `PARTIAL`. The step exits non-zero on `FAIL`, so a broken
PR fails the check; `PARTIAL` does not.

Without an API key the action stops with a configuration error. This repository's own
`.github/workflows/qa-changes.yml` guards the job with a preflight step that skips QA when
`LLM_API_KEY` is unset — worth copying if you are rolling the workflow out before the secret
is in place.

## Repository-specific QA guidelines

If `.agents/skills/qa-guide.md` exists, the agent reads it during setup and treats it as
authoritative for the repository — environment setup, priority scenarios, and anything it
should skip:

```markdown
---
name: qa-guide
description: Project-specific QA guidelines
---

# QA Guidelines for Widget

## Environment Setup
- Run `make setup`; the dev server runs on port 8080.

## Key Test Scenarios
- Verify /admin after any backend change.

## Known Limitations
- The payment module needs a Stripe test key — skip payment flows.
```

## How it works

`action.yml` runs `python -m qa_agent` against the checked-out PR. The agent has no third-party
dependencies — just the standard library.

| Module                | Role                                                             |
| --------------------- | ---------------------------------------------------------------- |
| `qa_agent/config.py`  | Resolves provider, endpoint and limits from the action inputs.    |
| `qa_agent/llm.py`     | OpenAI-compatible `/chat/completions` client, with retries.       |
| `qa_agent/github.py`  | Reads the PR and diff; posts or updates the report comment.       |
| `qa_agent/tools.py`   | The `bash` and `submit_report` tools, and report rendering.       |
| `qa_agent/prompt.py`  | The four-phase QA methodology given to the model.                 |
| `qa_agent/agent.py`   | The turn loop: call the model, run its tools, collect the report. |

Re-running on the same PR edits the existing report comment rather than adding another.

## Development

```bash
python -m unittest discover -s tests -v
```

The tests run the whole agent loop against a stub OpenAI-compatible server — no API key and no
network needed.
