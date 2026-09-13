# Test Report

Publishes JUnit XML as a check run with inline annotations, optionally comments the totals
on the pull request, and optionally forwards the results to a flaky-test service. It never
runs tests: running the suite is CI's job, and this composite only makes the outcome
readable and feeds quarantine data.

Every test framework can emit JUnit XML, which is what makes this stack-agnostic.

## Usage

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      checks: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
      - run: pytest --junitxml=reports/junit.xml
        continue-on-error: true
      - uses: khawjaahmad/github-workflows/test-report@v1
        with:
          junit_glob: reports/*.xml
```

`continue-on-error: true` on the test step lets the report publish when tests fail; the
report step then fails the job itself, so the failure is still red.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `junit_glob` | _(required)_ | JUnit XML files. |
| `name` | `Test results` | Check run name. |
| `comment` | `false` | Post or update a PR comment with the totals. |
| `flaky_upload` | `none` | `trunk` to upload to Trunk Flaky Tests. |
| `trunk_token` / `trunk_org` | _(none)_ | Trunk credentials, when `flaky_upload` is `trunk`. |
| `fail_on` | `FAIL` | `FAIL`, or `none` to stay advisory. |

Outputs: `status` (`PASS`, `FAIL`, or `SKIPPED` when no file matched) and `conclusion`.

The publisher is `EnricoMi/publish-unit-test-result-action` (Apache-2.0) and the uploader
`trunk-io/analytics-uploader` (MIT), both pinned by commit. The publisher is a Docker
action, so this composite runs on Linux runners. It needs `checks: write`, and
`pull-requests: write` when `comment` is on.
