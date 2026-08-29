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

1. Add your provider key as `LLM_API_KEY` and your model id as `LLM_MODEL` under
   **Settings → Secrets and variables → Actions**.
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
          model: ${{ secrets.LLM_MODEL }}
          provider: zai
```

Start with `fail_on: none` if you would rather see a few reports before the check can block a
merge.

## Providers

The agent speaks the OpenAI-compatible `/chat/completions` wire format, so any endpoint that
implements it works. `provider` selects the base URL:

| `provider` | Base URL                                                  | Source |
| ---------- | --------------------------------------------------------- | ------ |
| `zai`      | `https://api.z.ai/api/coding/paas/v4`                     | [Coding plan quick start](https://docs.z.ai/devpack/quick-start) |
| `openai`   | `https://api.openai.com/v1`                               | [Chat API](https://platform.openai.com/docs/api-reference/chat) |
| `gemini`   | `https://generativelanguage.googleapis.com/v1beta/openai` | [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai) |
| `custom`   | set `base_url` yourself                                    | — |

**There is no default model.** Model ids change faster than this action does, and a default
baked in here would quietly pin every repository to a superseded one. Supply it the same way
you supply the key, so the two move together:

```yaml
      - uses: khawjaahmad/github-workflows@main
        with:
          api_key: ${{ secrets.LLM_API_KEY }}
          model: ${{ secrets.LLM_MODEL || vars.LLM_MODEL }}
          provider: zai
```

The provider must support OpenAI-style function calling — the agent drives everything through
two tools, `bash` and `submit_report`. On z.ai, `tool_choice` supports `auto` only, which is
what the agent sends.

### Reasoning effort

`effort` is sent as `reasoning_effort`, passed through verbatim and **not validated locally**.
The accepted levels differ by provider and change with each model release, so the authority is
your provider's documentation, not this table:

- z.ai GLM-5.3: `low`, `high`, `max`, default `max`; reasoning cannot be disabled
  ([GLM-5.3](https://docs.z.ai/guides/llm/glm-5.3)).
- Gemini: `minimal`, `low`, `medium`, `high`, plus `none` on 2.5 models to disable thinking
  ([OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)).
- OpenAI: see the [reasoning guide](https://platform.openai.com/docs/guides/reasoning). Note
  that OpenAI recommends the Responses API over Chat Completions for reasoning models; this
  action uses Chat Completions because that is the protocol z.ai's coding plan exposes.

Leave `effort` unset to keep the model's own default.

### Reasoning content is preserved

Reasoning models return `reasoning_content` alongside the answer, and the agent sends it back
untouched on the next turn. z.ai's Preserved Thinking is enabled by default on the coding-plan
endpoint and requires the reasoning blocks to be returned "full, unmodified, and correctly
ordered" — missing or rewritten blocks "may degrade performance or prevent the feature from
taking effect", and cost the cache hits that make a long agent run affordable
([Thinking Mode](https://docs.z.ai/guides/capabilities/thinking-mode)). Providers that do not
return the field are unaffected.

This is why compaction drops reasoning blocks whole and only as a last resort, after tool
output and older prose have already gone.

### Context, and what happens when it runs out

`context_tokens` is the model's window. Set it and the agent compacts before it gets close,
measured against the `usage.prompt_tokens` the provider reports rather than a character
estimate. Left at `0` the agent does not guess a limit for a model it knows nothing about — it
compacts when the provider tells it the window was exceeded.

That signal is worth knowing about: on z.ai a context overflow comes back as a **successful**
response carrying `finish_reason: model_context_window_exceeded`, not as an HTTP error
([Chat Completion](https://docs.z.ai/api-reference/llm/chat-completion)). The agent also acts
on `length` (the reply was truncated — it tells the model to keep replies short rather than
reading the silence as a finished turn) and on `sensitive` / `content_filter` (it stops and
reports).

### Retries

Transient failures are retried with jittered backoff, honouring `Retry-After`. Quota and
subscription failures are not — z.ai returns HTTP 429 for an exhausted plan, an expired
subscription and a model outside your plan as well as for genuine rate limiting, and only
codes 1302 and 1305 are worth waiting on
([Errors](https://docs.z.ai/api-reference/api-code)). Retrying the rest would spend the job's
time budget and end with a vaguer message than the provider already gave you.

## Inputs

| Input                     | Default              | Description                                                          |
| ------------------------- | -------------------- | -------------------------------------------------------------------- |
| `api_key`                 | _(required)_         | Provider API key.                                                     |
| `model`                   | _(required)_         | Model id. No default — supply it from a secret or variable.           |
| `provider`                | `zai`                | `zai`, `openai`, `gemini` or `custom`. Selects the base URL.          |
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
| `context_tokens`          | `0`                  | The model's context window. `0` means compact only on overflow.       |
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
overflow the model's context window. Set `context_tokens` to compact before the window is reached rather than after.

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
