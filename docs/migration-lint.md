# Migration Lint

Runs squawk over the SQL migration files a pull request adds or changes, and runs the
repository's own schema-drift check when it has one. Squawk catches the operations that
lock a live Postgres table or fail on real data: adding a `NOT NULL` column without a
default, a non-concurrent index, a type change, a table rename.

## Quick start

```yaml
name: Migration Lint

on:
  pull_request:
  merge_group:

jobs:
  migrations:
    permissions:
      contents: read
    uses: khawjaahmad/github-workflows/.github/workflows/migration-lint.yml@v1
    with:
      sql_globs: db/migrate/*.sql
      check_command: alembic check
```

## What it does

1. Lists the SQL files in scope: with `scope: changed` (the default), the files the pull
   request added, modified or renamed relative to the base branch that match `sql_globs`;
   with `scope: all`, every matching file in the tree.
2. Runs `squawk` over them when `dialect` is `postgres`. Any finding fails the check;
   squawk's report with the rule name and a fix suggestion is in the log.
3. Runs `check_command` when set, for example `alembic check`, `prisma migrate diff
   --exit-code`, `django manage.py makemigrations --check`, or `rails db:migrate && git diff
   --exit-code db/schema.rb`. A non-zero exit fails the check.

With no matching files and no `check_command`, the job skips green with a note.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `sql_globs` | `*migrations/*.sql`, `migrations/*.sql`, `*migrate/*.sql`, `db/*.sql` | Comma- or newline-separated globs, matched against the full path with `fnmatch`. |
| `dialect` | `postgres` | `postgres` runs squawk; any other value runs only `check_command`. |
| `scope` | `changed` | `changed` or `all`. |
| `base_ref` | PR base branch | Diff base for `changed`. The reusable workflow passes the PR or merge-queue base. |
| `check_command` | _(none)_ | Repository-specific drift check. |
| `command_timeout` | `600` | Seconds per command. |
| `fail_on` | `FAIL` | `FAIL`, or `none` to stay advisory. |

Outputs: `status` (`PASS`, `FAIL`, `SKIPPED`) and `files` (how many were in scope).

squawk 2.65.0 (Apache-2.0) is installed from its GitHub release. MySQL and other dialects
have no bundled linter; the research report lists atlas as the format-agnostic option if
one is wanted later.
