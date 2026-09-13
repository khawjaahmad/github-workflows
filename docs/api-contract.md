# API Contract

Checks an HTTP API against its own specification. Three layers, each on by default when its
input is present, each skipped with a note when it is not:

1. **Lint** the OpenAPI document with vacuum.
2. **Breaking changes** between the base branch's spec and the pull request's, with oasdiff
   (OpenAPI) or graphql-inspector (GraphQL SDL).
3. **Conformance** of a running server to the spec, with schemathesis fuzzing every
   operation, plus optional Hurl request files.

Layers 1 and 2 need only a file in the repository. Layer 3 needs a URL, or a start command
and a ready URL, the same contract [Smoke](smoke.md) uses. No language or framework is
assumed anywhere.

## Quick start

```yaml
name: API Contract

on:
  pull_request:
  merge_group:

jobs:
  api:
    permissions:
      contents: read
    uses: khawjaahmad/github-workflows/.github/workflows/api-contract.yml@v1
    with:
      spec_path: api/openapi.yaml
      start_command: make run
      ready_url: http://127.0.0.1:8080/health
```

With no `spec_path`, the action looks for `openapi.yaml`, `openapi.yml`, `openapi.json`,
`swagger.yaml`, `swagger.yml`, `swagger.json`, `schema.graphql` or `schema.graphqls` at the
repository root, and skips green with a note when none exists.

## What each layer does

**Lint.** `vacuum lint --fail-severity error` with vacuum's recommended ruleset, or the
repository's own `.vacuum.yaml` or `.spectral.yaml` when present. Warnings are reported
in the log but do not fail. GraphQL schemas are not linted.

**Breaking changes.** The base branch's copy of the spec is read with `git show`, fetching
the branch first if the checkout does not have it. When the base branch has no such file
(a new API), the layer is skipped with a note. OpenAPI: `oasdiff breaking --fail-on ERR`.
GraphQL: `graphql-inspector diff`, which exits non-zero on breaking changes. Both tools'
output goes to the log.

**Conformance.** `schemathesis run <spec> --url <api url> --checks all --max-examples N`,
with a JUnit report under the artifacts. The API URL is `base_url` when given, otherwise
the origin of `ready_url` (so a `ready_url` of `http://127.0.0.1:8080/health` fuzzes
`http://127.0.0.1:8080`). For a GraphQL spec the endpoint URL itself is the schema source.
`auth_header` is passed on every request. When `hurl_dir` is set, the `.hurl` files run
afterwards with that same API URL as the `base_url` variable.

The verdict is `PASS` when every layer that ran passed, `FAIL` otherwise, `SKIPPED` when no
spec was found. Each layer is a row in the job summary.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `spec_path` | auto-detected | OpenAPI document or GraphQL SDL. |
| `base_ref` | PR base branch | Git ref holding the base spec. The reusable workflow passes the PR or merge-queue base. |
| `lint` | `true` | Lint with vacuum. |
| `breaking` | `true` | Fail on breaking changes. |
| `base_url` | _(none)_ | Running server to fuzz. |
| `start_command` / `compose_file` | _(none)_ | Start the server here instead; needs `ready_url`. |
| `ready_url` | `base_url` | URL polled until the server answers. |
| `ready_timeout` | `120` | Seconds to wait. |
| `auth_header` | _(none)_ | Header for every fuzzing request. A secret on the reusable workflow. |
| `max_examples` | `50` | Schemathesis examples per operation. |
| `hurl_dir` | _(none)_ | Directory of `.hurl` files. |
| `setup_command` | _(none)_ | Runs before the server starts. |
| `command_timeout` | `600` | Seconds per tool run. |
| `fail_on` | `FAIL` | `FAIL`, or `none` to stay advisory. |

Outputs: `status`, `spec_path`, `kind` (`openapi` or `graphql`).

Tool versions are pinned in the action: vacuum 0.30.3, oasdiff 1.31.0, schemathesis 4.27.0,
@graphql-inspector/cli 7.0.0, hurl 8.0.1. All are MIT or Apache-2.0.

## Merge queues

Runs on `merge_group`; the base spec comes from the queue's base branch.
