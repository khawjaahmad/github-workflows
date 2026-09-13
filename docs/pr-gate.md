# PR Gate

Deterministic hygiene checks on a pull request. It reads the PR's metadata and diff from
the API and, with a checkout, scans the commits for secrets and lints workflow changes.
Nothing in it depends on the language or framework of the repository.

The checks target what goes wrong most often in agent-authored pull requests: oversized
diffs, files touched outside the task, TODOs left behind, secrets committed, workflow files
edited, and dependency changes nobody reviewed.

## Quick start

```yaml
name: PR Gate

on:
  pull_request:
    types: [opened, synchronize, reopened, edited]
  merge_group:

jobs:
  gate:
    permissions:
      contents: read
      pull-requests: write
    uses: khawjaahmad/github-workflows/.github/workflows/pr-gate.yml@v1
    with:
      require_tests_for: src/**
      forbidden_paths: .github/**,.mcp.json
```

`pull-requests: write` is only needed for `label_agent_prs`; drop it to `read` otherwise.

## Checks

| Check | Default | What fails it |
| --- | --- | --- |
| size | `max_lines_changed: 1500` | More lines added plus deleted than the limit, after removing files matching `ignore_globs` (lockfiles by default). `0` disables. |
| paths | off | Any changed file outside `allowed_paths`, or inside `forbidden_paths`. |
| todos | on | A line added by the diff that contains `TODO` or `FIXME`. |
| tests | off | A changed file matching `require_tests_for` with no changed file matching `test_globs`. |
| secrets | on | gitleaks 8.30.1 finds a secret in the commits between base and head. Needs the checkout the reusable workflow does (`fetch-depth: 0`). |
| workflow lint | on, when `.github/**` changed | actionlint 1.7.12 or zizmor 1.30.1 (medium and above) reports a problem. Needs a checkout. |
| dependency review | off | `actions/dependency-review-action` finds a vulnerable or disallowed dependency. Runs only when the repository's dependency graph is enabled; otherwise it is skipped with a note. |
| agent label | on | Never fails. Adds `agent_label` when the author login is in `agent_logins`, or a commit carries a `Co-authored-by` trailer naming Claude, Codex, Copilot, Cursor, Devin or Gemini. |

Globs are shell-style, matched against the full path with `fnmatch`: `*` matches across
`/`, so `src/*` and `src/**` both match `src/a/b.py`. Lists may be separated by commas or
newlines.

The verdict is `PASS` when every check that ran passed, otherwise `FAIL`. Each check is one
row in the job summary with a line of detail.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `max_lines_changed` | `1500` | Fail above this many added plus deleted lines. `0` disables. |
| `ignore_globs` | lockfiles, `*.snap`, `*.min.js` | Files excluded from the line count. |
| `allowed_paths` | _(none)_ | Every changed file must match one. |
| `forbidden_paths` | _(none)_ | No changed file may match one. |
| `no_new_todos` | `true` | Fail when the diff adds a TODO or FIXME. |
| `require_tests_for` | _(none)_ | Source globs whose change needs a test change. |
| `test_globs` | `test*`, `*/test*`, `*_test.*`, `*.test.*`, `*.spec.*`, `*/spec/*`, `*/__tests__/*` | What counts as a test file. |
| `secrets` | `true` | Scan commits with gitleaks. |
| `workflow_lint` | `true` | actionlint and zizmor when `.github/` changed. |
| `dependency_review` | `false` | Dependency review when the dependency graph is enabled. |
| `label_agent_prs` | `true` | Label agent-authored PRs. |
| `agent_logins` | `copilot-swe-agent[bot]`, `devin-ai-integration[bot]` | Bot logins treated as agents. |
| `agent_label` | `ai-generated` | The label. |
| `fail_on` | `FAIL` | `FAIL`, or `none` to stay advisory. |

The action also takes `github_token`, `pr_number`, `github_api_url` and `python_version`,
with the same meaning as on QA Changes. The reusable workflow adds `runs_on`.

Outputs: `status` (`PASS`, `FAIL`) and `is_agent_pr` (`true`, `false`).

## Merge queues

On `merge_group` the pull request number is read from the queue branch name and the checks
run against the merge result. `label_agent_prs` still targets the pull request.
