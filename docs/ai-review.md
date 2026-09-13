# AI Second Opinion

Asks Claude Code to verify the pull request by running it and to report what it ran and
what happened. It is a second model looking at the change with a different harness from
[QA Changes](qa-changes.md), and it is always advisory: the job never fails on findings,
only on its own errors, and those are caught too.

This overlaps with QA Changes on purpose. QA Changes talks to any OpenAI-compatible
provider through this repository's own agent loop; this workflow uses Anthropic's action
and model. Run one or both.

## Quick start

```yaml
name: AI Second Opinion

on:
  pull_request:

jobs:
  review:
    permissions:
      contents: read
      pull-requests: write
      issues: write
      id-token: write
    uses: khawjaahmad/github-workflows/.github/workflows/ai-review.yml@v1
    secrets:
      anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

## What it does

Checks out the pull request with history and runs `anthropics/claude-code-action` in
prompt mode. The default prompt asks the agent to work out how the project builds and runs
from the files present, run the commands that exercise the changed behaviour, and post one
comment with what it ran, the real output, and whether the change works, under 300 words
and without style commentary. Tools are limited to `Bash`, `Read`, `Glob`, `Grep` and the
comment updater; `--max-turns` bounds the run.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `prompt` | the verification prompt above | What to ask. |
| `max_turns` | `40` | Turn budget. |
| `model` | action default | Model id passed to the action. |
| `runs_on` | `ubuntu-latest` | Runner label. |
| `timeout_minutes` | `30` | Job timeout. |

Secret: `anthropic_api_key` (required).

## Notes

- Fork pull requests get no secrets, so the job cannot run for them; the caller's trigger
  should stay `pull_request`, never `pull_request_target`.
- This workflow has no self-test in this repository (no Anthropic key here). The action is
  pinned to its `v1` commit and its inputs follow its published README.
