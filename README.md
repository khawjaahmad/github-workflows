# qa-changes

Automated QA testing for pull requests: an agent that **runs the software** instead of
reading the diff. It sets up the repository, exercises the changed behaviour as a real user
would (CLI, HTTP, browser), and posts a structured QA report as a PR comment.

It is not a code reviewer and not a test runner — those jobs already have owners.

Nothing in the agent assumes a language, a framework or a package manager. It is told what
build files and tooling are actually present in the checkout and works it out from there, so
the same action drops into a Go service, a Rails app or a Gradle monorepo unchanged.

## What it does

Four phases, on every run:

1. **Understand** — read the PR title, description and diff; classify the change and identify
   the entry points it touches.
2. **Setup** — bootstrap the repository from the build files and documentation that are
   actually there.
3. **Exercise** — start servers, make real HTTP requests, run real commands, drive a browser.
   For a bug fix, reproduce the bug on the base commit first, then verify the fix on the PR
   commit.
4. **Report** — post a structured report with the evidence and a verdict of PASS, FAIL or
   PARTIAL.

It runs the application and interacts with it, makes real requests, reproduces bugs and
verifies fixes end to end, and quotes real command output as evidence. It does not run the
test suite, lint, format or type-check, or review code style — and `--help` and `--dry-run`
are never accepted as substitutes for running the thing.

| Change type       | How it is tested                                                     |
| ----------------- | -------------------------------------------------------------------- |
| Frontend / UI     | Start the dev server, load pages, verify rendering and interactions.  |
| CLI               | Run commands with realistic arguments; check output and exit codes.   |
| API / backend     | Start the server, issue requests, verify responses and side effects.  |
| Bug fix           | Reproduce on the base commit, verify the fix on the PR commit.        |
| Library / SDK     | Write and run a short script that calls the changed functions.        |
| Config / infra    | Apply the config and show the resulting behaviour differs.            |

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
          fetch-depth: 0 # so the agent can check out the base commit for before/after testing
      - uses: khawjaahmad/github-workflows@main
        with:
          api_key: ${{ secrets.LLM_API_KEY }}
          provider: zai
```

Start with `fail_on: none` if you would rather see a few reports before the check can block a
merge.

## Providers

The agent speaks the OpenAI-compatible `/v1` chat-completions wire format, so any endpoint
that implements it works. `provider` picks the defaults:

| `provider` | Base URL                                                 | Default `model`    |
| ---------- | -------------------------------------------------------- | ------------------ |
| `zai`      | `https://api.z.ai/api/coding/paas/v4`                    | `glm-5.3`          |
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

### Reasoning effort

`effort` is sent as the OpenAI-standard `reasoning_effort` field, passed through verbatim.
Leave it unset to keep the model's own default. The levels are **provider-specific**:

| Provider          | Accepted levels                    | Default |
| ----------------- | ---------------------------------- | ------- |
| z.ai `glm-5.3`    | `low`, `high`, `max`               | `max`   |
| OpenAI            | `low`, `medium`, `high`, `xhigh`   | model-dependent |
| Gemini            | `low`, `medium`, `high`            | model-dependent |

Values are not validated locally, so a level a provider does not recognise is the provider's
to reject — or to ignore. `glm-5.3` in particular **silently falls back to `max`** for any
unrecognised value rather than erroring, so `xhigh` there is a no-op, not a failure. GLM-5.3
also always reasons; thinking cannot be disabled.

## Inputs

| Input                     | Default              | Description                                                          |
| ------------------------- | -------------------- | -------------------------------------------------------------------- |
| `api_key`                 | _(required)_         | Provider API key.                                                     |
| `provider`                | `zai`                | `zai`, `openai`, `gemini` or `custom`.                                |
| `model`                   | per provider         | Model id.                                                             |
| `base_url`                | per provider         | OpenAI-compatible base URL; required for `custom`.                    |
| `effort`                  | _(model default)_    | `reasoning_effort`, passed through verbatim.                          |
| `github_token`            | `github.token`       | Reads the PR and posts the report; needs `pull-requests: write`.       |
| `github_api_url`          | `github.api_url`     | REST API root — set for GitHub Enterprise Server.                     |
| `pr_number`               | from the event       | PR to QA. Required when not triggered by `pull_request`.              |
| `setup_command`           | _(none)_             | Command the agent runs before it starts testing.                      |
| `max_turns`               | `40`                 | Model turns before the agent is told to wrap up.                      |
| `time_budget`             | `1500`               | Wall-clock seconds before the agent is told to wrap up. `0` disables. |
| `command_timeout`         | `300`                | Default per-command timeout, in seconds.                              |
| `request_timeout`         | `600`                | Timeout for each request to the model endpoint.                       |
| `max_context_chars`       | `240000`             | Conversation size before the oldest command output is elided.         |
| `post_comment`            | `true`               | Set to `false` to log the report without commenting.                  |
| `fail_on`                 | `FAIL`               | Verdicts that fail the check: a list, or `none`.                      |
| `upload_artifacts`        | `true`               | Upload what the agent saved as a workflow artifact.                   |
| `artifact_retention_days` | `14`                 | How long those artifacts are kept.                                    |
| `python_version`          | `3.11`               | Python used to run the agent.                                         |

Output `status` is `PASS`, `FAIL` or `PARTIAL`.

## Budgets, and why there is always a report

A pull request with a red check and no explanation is worse than no QA at all, so every way a
run can end produces a report:

- The agent submits its own report — the normal case.
- `max_turns` or `time_budget` runs out. The agent is told to wrap up and gets two more turns
  to write the report itself; if it still does not, the action writes a `PARTIAL` report
  listing the commands it had actually run.
- The provider fails, times out, or rejects the request. Retries are exhausted first
  (honouring `Retry-After`), then the same `PARTIAL` fallback is posted with the error.
- The comment cannot be posted — a missing `pull-requests: write`, say. The run is annotated
  with an error and the report is still written to the job summary and the log.

Keep `time_budget` comfortably under the job's `timeout-minutes`: a job the runner kills has
no chance to report anything. The default pair is 25 minutes of agent against a 30-minute job.

The transcript is compacted as it grows, oldest command output first, so a long run cannot
overflow the model's context window. Lower `max_context_chars` for a smaller model.

## Screenshots, logs and other evidence

A PR comment cannot embed an image the agent produced. Anything the agent saves to
`$QA_ARTIFACTS_DIR` is uploaded as a workflow artifact, listed by name in the report, and
linked back to the run — so a screenshot of a broken page reaches the reviewer.

The GitHub-hosted Ubuntu runners ship Chrome, Chromedriver and Firefox, so a UI change can be
driven headlessly without installing a browser first.

## What the agent's commands can see

The agent's instructions come partly from the pull request under test, so its shell is not
given anything the action introduced. `QA_*`, `INPUT_*`, `LLM_API_KEY`, `GITHUB_TOKEN` and the
runner's `ACTIONS_*` tokens are all removed from the environment of every command it runs;
only `QA_ARTIFACTS_DIR` is handed back. Variables your own workflow sets are left alone, since
a project may need them to boot.

Two things worth knowing when you enable this:

- On `pull_request`, secrets are not available to pull requests from forks, so the workflow
  above simply will not run for them. That is the safe default. Do not "fix" it by switching
  to `pull_request_target`, which would run fork code with your secrets.
- A command that hangs is killed along with everything it spawned, so a stuck dev server
  cannot hold the runner open.

## Repository-specific QA guidelines

If `.agents/skills/qa-guide.md` exists it is read before the run starts and included in the
agent's instructions as authoritative for the repository — environment setup, priority
scenarios, and anything it should skip:

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

`action.yml` runs `python -m qa_agent` against the checked-out PR. The agent has no
third-party dependencies — just the standard library.

| Module                     | Role                                                             |
| -------------------------- | ---------------------------------------------------------------- |
| `qa_agent/config.py`       | Resolves provider, endpoint and budgets from the action inputs.   |
| `qa_agent/llm.py`          | OpenAI-compatible `/chat/completions` client, with retries.       |
| `qa_agent/github.py`       | Reads the PR and diff; posts or updates the report comment.       |
| `qa_agent/environment.py`  | Fingerprints the checkout so the prompt assumes no stack.         |
| `qa_agent/prompt.py`       | The four-phase QA methodology given to the model.                 |
| `qa_agent/tools.py`        | The `bash` and `submit_report` tools, and report rendering.       |
| `qa_agent/history.py`      | Keeps the transcript inside the context window.                   |
| `qa_agent/agent.py`        | The turn loop: call the model, run its tools, collect the report. |

Re-running on the same PR edits the existing report comment rather than adding another, and
finds it however far down the thread it has been pushed.

## Development

```bash
python -m unittest discover -s tests -v
```

The tests run the whole action — configuration, agent loop, tools and comment upsert — against
stub OpenAI-compatible and GitHub servers. No API key and no network needed.
