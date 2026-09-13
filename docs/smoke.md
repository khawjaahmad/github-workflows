# Smoke

Starts the software, waits until it answers, then checks a list of routes and commands.
Deterministic, no model, no secrets; finishes in the time the software takes to boot. It is
the check that should run before [QA Changes](qa-changes.md): if the software does not
start, the agent has nothing to test.

Nothing here assumes a stack. The consumer says how the software starts (a command or a
compose file), where it answers (`ready_url`), and what "works" means (`routes`, `commands`).

## Quick start

```yaml
name: Smoke

on:
  pull_request:
  merge_group:

jobs:
  smoke:
    permissions:
      contents: read
    uses: khawjaahmad/github-workflows/.github/workflows/smoke.yml@v1
    with:
      setup_command: npm ci
      start_command: npm start
      ready_url: http://127.0.0.1:3000/
      routes: |
        /
        /health
        /admin=302
      commands: |
        npx mycli --version
```

The reusable workflow checks out the merge result and calls the composite action
`khawjaahmad/github-workflows/smoke@v1`. Use the action directly when the checkout needs
services, a container, or a different runner.

## What it does

1. Runs `setup_command`, if given. A non-zero exit stops the run with `FAIL`.
2. Starts the software: `start_command` in the background, in its own process group, with
   stdout and stderr captured to `smoke-server.log`; or `docker compose -f <file> up --detach
   --wait --wait-timeout <ready_timeout>`.
3. Polls `ready_url` every two seconds until it answers with any status below 500, or until
   `ready_timeout` passes. If the server process exits first, that is reported with its exit
   code and the log is uploaded.
4. Checks each route. A route is `path[=expected_status]`; the default expectation is 200.
   Relative paths are joined to `ready_url`; full URLs are used as given. Redirects are not
   followed, so `=302` is a valid expectation.
5. Runs each command with `bash -c`; each must exit 0 within `command_timeout`.
6. Runs `hurl --test` over `hurl_dir` when set, with `base_url` as a variable and a JUnit
   report in the artifacts.
7. Stops the server (or `docker compose down --volumes --remove-orphans`), writes a results
   table to the job summary, and uploads the logs as a workflow artifact.

Every check appears in the summary table with its outcome and one line of detail, whether or
not it passed. The verdict is `PASS` when every check passed, `FAIL` otherwise, and
`SKIPPED` when nothing was configured, in which case the summary says so and the job stays
green.

The commands run with the same environment scrubbing as the QA agent: `QA_*`, `INPUT_*`,
tokens and the runner's env files are removed.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `start_command` | _(none)_ | Command that starts the software in the background. Exclusive with `compose_file`. |
| `compose_file` | _(none)_ | Compose file brought up with `docker compose up --wait`. Services need a `healthcheck` for `--wait` to mean anything. |
| `ready_url` | _(none)_ | URL polled until it answers with a status below 500. Required when `routes` or `hurl_dir` is set. |
| `ready_timeout` | `120` | Seconds to wait for readiness. |
| `routes` | _(none)_ | Newline-separated `path[=expected_status]` entries. |
| `commands` | _(none)_ | Newline-separated commands that must exit 0. |
| `hurl_dir` | _(none)_ | Directory of `.hurl` files. Hurl 8.0.1 is installed on demand. |
| `setup_command` | _(none)_ | Command run before anything starts. Give it the same value as QA Changes' `setup_command`. |
| `command_timeout` | `300` | Seconds allowed for the setup command and each smoke command. |
| `fail_on` | `FAIL` | `FAIL`, or `none` to keep the check green while you read a few results. |
| `upload_artifacts` | `true` | Upload server logs and reports. |
| `python_version` | `3.11` | Python used to run the check. |

The reusable workflow adds `runs_on` (default `ubuntu-latest`) and `timeout_minutes`
(default `15`).

Outputs: `status` (`PASS`, `FAIL`, `SKIPPED`) and `ready_url`.

## Chaining into QA Changes

```yaml
jobs:
  smoke:
    uses: khawjaahmad/github-workflows/.github/workflows/smoke.yml@v1
    with:
      start_command: make run
      ready_url: http://127.0.0.1:8080/health
  qa:
    needs: smoke
    if: needs.smoke.outputs.status == 'PASS'
    permissions:
      contents: read
      pull-requests: write
    uses: khawjaahmad/github-workflows/.github/workflows/qa.yml@v1
    secrets: inherit
    with:
      model: ${{ vars.LLM_MODEL }}
      setup_command: make deps
```

## Merge queues

The workflow runs on `merge_group` events unchanged: it checks out `github.sha`, which is
the merge result, and needs nothing from the pull request payload.
