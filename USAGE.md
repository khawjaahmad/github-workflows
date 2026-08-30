# Using qa-changes in your repositories

How to put this action on a repository other than this one. For what the agent does and how
it is configured, see [README.md](README.md); this file is about rollout.

## 1. Configure the provider once, for the whole org

Both values the action needs are configuration that changes over time, so neither is baked
into the action. Set them at the organization level and every repository inherits them —
adding QA to a new repo then costs one file with nothing to fill in.

In your organization's **Settings → Secrets and variables → Actions**:

| Name          | Tab       | Value            |
| ------------- | --------- | ---------------- |
| `LLM_API_KEY` | Secrets   | your provider key |
| `LLM_MODEL`   | Variables | e.g. `glm-5.3`   |

Set each one's repository access to the repos that should run QA. The model id is not
sensitive, and putting it in **Variables** means you can see what you are pinned to without
opening a run log. A repository-level secret or variable of the same name overrides the
organization one, which is how you give a single repo a different model.

## 2. Add the workflow

Copy this into `.github/workflows/qa-changes.yml`:

```yaml
name: QA Changes

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write

concurrency:
  group: qa-changes-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  qa:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      # Pull requests from forks get no secrets. Without this guard the action
      # would fail configuration and put a red check on every fork contribution.
      - name: Check that the provider is configured
        id: preflight
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          LLM_MODEL: ${{ vars.LLM_MODEL }}
        run: |
          missing=""
          if [ -z "$LLM_API_KEY" ]; then missing="$missing LLM_API_KEY"; fi
          if [ -z "$LLM_MODEL" ]; then missing="$missing LLM_MODEL"; fi
          if [ -n "$missing" ]; then
            echo "Skipping QA — not set:$missing" | tee -a "$GITHUB_STEP_SUMMARY"
            echo "ready=false" >> "$GITHUB_OUTPUT"
          else
            echo "ready=true" >> "$GITHUB_OUTPUT"
          fi

      - uses: actions/checkout@v4
        if: steps.preflight.outputs.ready == 'true'
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          # Full history, so the agent can check out the base commit and compare
          # behaviour before and after a bug fix.
          fetch-depth: 0

      - uses: khawjaahmad/github-workflows@v1
        if: steps.preflight.outputs.ready == 'true'
        with:
          api_key: ${{ secrets.LLM_API_KEY }}
          model: ${{ vars.LLM_MODEL }}
          provider: zai
          effort: max
          fail_on: none
```

Three things in there are load-bearing:

- **`pull-requests: write`** — without it the report cannot be posted. The run still succeeds
  and writes the report to the job summary, with an error annotation explaining why.
- **`fetch-depth: 0`** — without it the agent cannot reach the base commit, and bug-fix PRs
  lose the before/after comparison that is the most convincing evidence it produces.
- **`timeout-minutes: 30`** — keep it comfortably above `time_budget` (default 1500s). A job
  the runner kills is signalled first, so a report still gets out, but the agent should be
  finishing on its own budget rather than on the runner's.

Start at `fail_on: none` so the check reports without blocking merges. Drop the line once you
have read a few reports from that codebase and trust the verdicts.

## 3. Pin a version

`@main` tracks whatever lands in this repository, so a change here changes QA behaviour
everywhere at once. Tag instead:

```bash
git tag -a v1.0.0 -m "qa-changes v1.0.0" && git push origin v1.0.0
git tag -f v1 && git push -f origin v1          # moving major, the usual convention
```

Consumers then use `@v1` and get fixes without surprises, or `@v1.0.0` to freeze exactly.
**`v1` does not exist yet** — until it is created, the examples above need `@main`.

## 4. Tune it for the repository

Everything here is optional. All inputs are listed in [action.yml](action.yml).

**`context_tokens`** — the model's context window. Set it (`1000000` for GLM-5.3) and the
agent compacts the transcript before the window is reached, measured against the token counts
the provider reports. Left at `0` it compacts only after the provider says the window was
exceeded, which works but wastes a turn.

**`setup_command`** — one command that bootstraps the repo, run before testing starts.

**`.agents/skills/qa-guide.md`** — the highest-leverage thing you can add. It is read before
the run and given to the agent as authoritative for the repository:

```markdown
# QA Guidelines for Widget

## Environment Setup
- Run `make setup`; the dev server runs on port 8080 and needs `DATABASE_URL`.

## Key Test Scenarios
- Verify /admin after any backend change.

## Known Limitations
- The payment module needs a Stripe test key — skip payment flows.
```

Write it for someone who has never seen the project. If a human could not get the app running
from your README alone, the agent will not either, and you will get PARTIAL reports saying so.

**`max_turns`** — the default is 40. On this repository every run has spent all 40 turns while
using about half its time budget, so if reports keep listing things the agent never reached,
this is the lever rather than `time_budget`.

**`github_api_url`** — defaults to the host the workflow runs on, so GitHub Enterprise Server
needs no configuration.

## 5. What the runner already provides

The action installs only Python. Everything else the agent uses comes from the runner image,
so `ubuntu-latest` already covers most stacks without an install step. On the `ubuntu-24.04`
image the agent has Node, Python, Go, Ruby, PHP, Java, .NET, Rust, Swift and Kotlin; Docker
with Compose; PostgreSQL and MySQL (installed but not started — `sudo systemctl start
postgresql.service`); and nginx and Apache.

For UI changes it has Google Chrome, Chromium, Firefox and Edge with their drivers, Selenium,
and `xvfb`, exposed through `CHROMEWEBDRIVER`, `GECKOWEBDRIVER` and `SELENIUM_JAR_PATH`. No
browser install step is needed. Screenshots the agent saves to `$QA_ARTIFACTS_DIR` are
uploaded to the workflow run and listed by name in the report — a PR comment cannot embed an
image the agent produced.

Versions move with the image; the authority is the
[runner-images readme](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)
for whichever image your run used, named at the top of every job log.

## 6. Reading the result

The report is posted as a PR comment and re-run in place — a second push edits the same
comment rather than adding another.

| Verdict   | Means                                                                    |
| --------- | ------------------------------------------------------------------------ |
| `PASS`    | The changed behaviour was exercised and works as claimed.                 |
| `FAIL`    | The agent demonstrated it is broken. The evidence section shows how.      |
| `PARTIAL` | Some scenarios passed, others failed or could not be run.                 |

`PARTIAL` is common and is not by itself a problem — read **Not Tested**, which says what the
agent did not reach and why. Running out of turns, a missing external service and an
unbuildable branch all land here and mean different things.

Exit codes: `0` normally, `1` when the verdict is in `fail_on`, `2` for a configuration error
before any QA could run. The `status` output carries the verdict for later steps.

## 7. When QA does not run

**The check is skipped.** `LLM_API_KEY` or `LLM_MODEL` is missing, or the PR is from a fork —
forks get no secrets, which is the safe default. Do not switch to `pull_request_target` to
work around it: that runs the fork's code with your credentials.

**Exit 2 with a configuration error.** The message names the input. The most common is a
missing `model`, which has no default by design.

**A report with no comment, and an error annotation.** The job lacks `pull-requests: write`.
The report is still in the job summary.

**The report says the agent could not start the app.** Its setup instructions came from your
README, `AGENTS.md` or QA guide. Add what is missing to the QA guide.

## 8. What it will not do

It does not run your test suite, linters, formatters or type checkers — that is CI's job — and
it does not review code style or structure, which is code review's job. It will not accept
`--help` or `--dry-run` in place of running the thing.

It runs commands from a branch under review, so treat it as you would any CI that executes PR
code. The action removes its own secrets from the environment of every command the agent runs
— `QA_*`, `INPUT_*`, `LLM_API_KEY`, `GITHUB_TOKEN` and the runner's `ACTIONS_*` tokens — but
variables your workflow sets are deliberately left in place, because a project may need them
to boot. Do not put credentials there that you would not want a PR author to reach.
