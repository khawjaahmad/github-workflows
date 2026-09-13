# Diff Scope

A composite action, not a workflow: it produces outputs other jobs branch on. It answers two
questions about a pull request. Which named parts of the repository did it touch, and how
much of the code it added is covered by tests.

Both answers need only git and, for coverage, a report in a standard format. No stack is
assumed.

## Usage

```yaml
jobs:
  scope:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: read
    outputs:
      changes: ${{ steps.scope.outputs.changes }}
    steps:
      - uses: actions/checkout@v4
      - id: scope
        uses: khawjaahmad/github-workflows/diff-scope@v1
        with:
          filters: |
            backend:
              - 'services/**'
            docs:
              - '**/*.md'

  smoke:
    needs: scope
    if: contains(fromJSON(needs.scope.outputs.changes), 'backend')
    uses: khawjaahmad/github-workflows/.github/workflows/smoke.yml@v1
    with:
      start_command: make run
      ready_url: http://127.0.0.1:8080/
```

Skipping a job this way keeps a required check green on docs-only changes, which a
`paths-ignore` trigger filter does not: a workflow that never ran leaves its required check
pending.

## Patch coverage

Run your tests with coverage in an earlier step, then point the action at the report:

```yaml
      - run: pytest --cov --cov-report=xml
      - id: scope
        uses: khawjaahmad/github-workflows/diff-scope@v1
        with:
          coverage_file: coverage.xml
          patch_coverage_min: 80
```

`diff-cover` 10.5.1 computes the percentage of lines added by the pull request that the
report marks as covered, writes its Markdown summary to the job summary, and the action
fails when the percentage is below `patch_coverage_min`. Cobertura, LCOV, Clover and JaCoCo
reports are accepted, which covers the default output of nearly every language's coverage
tool. Paths in the report must be relative to the repository root.

The comparison needs the base branch locally; the action fetches it when the checkout did
not. A `fetch-depth: 0` checkout avoids that extra fetch.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `filters` | _(none)_ | YAML map of filter name to path globs, as `dorny/paths-filter` takes it. |
| `coverage_file` | _(none)_ | Coverage report from an earlier step. |
| `patch_coverage_min` | `0` | Minimum percent of added lines that must be covered. `0` reports without failing. |
| `compare_branch` | `origin/<base branch>` | Branch the coverage diff is computed against. |
| `fail_on` | `FAIL` | `FAIL`, or `none` to stay advisory. |

Outputs: `changes` (JSON list of matched filter names), `patch_coverage` (percent) and
`status` (`PASS`, `FAIL`, `SKIPPED`). With neither input set the action is skipped with a
note.

`dorny/paths-filter` is pinned by commit SHA. On `pull_request` it reads the changed files
from the API and needs no checkout; on `push` and `merge_group` it needs one.
