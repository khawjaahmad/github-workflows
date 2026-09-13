# Build

Proves the pull request still builds. The action looks at the files the repository actually
has, picks the idiomatic build command, runs it, and reports `PASS`, `FAIL` or `SKIPPED`.
No stack is assumed; when nothing is recognised the job stays green and the summary says
what to set.

## Quick start

```yaml
name: Build

on:
  pull_request:
  merge_group:

jobs:
  build:
    permissions:
      contents: read
    uses: khawjaahmad/github-workflows/.github/workflows/build.yml@v1
```

With a `build_command` the detection is skipped and that command is what runs:

```yaml
    with:
      build_command: make -C services/api release
```

## Detection order

The first match wins. Files are looked for in `working_directory` (default the repository
root).

| Detected | Trigger | Command |
| --- | --- | --- |
| `custom` | `build_command` input | the input |
| `make` | `Makefile` with a `build:` target | `make build` |
| `node` | `package.json` | install from the lockfile (`npm ci`, `pnpm install --frozen-lockfile`, `yarn install`, `bun install --frozen-lockfile`, else `npm install`), then `<pm> run build` if a `build` script exists |
| `python` | `pyproject.toml` with `[project]` or `[build-system]`, else `requirements.txt` | `python -m pip install .` or `python -m pip install -r requirements.txt` |
| `go` | `go.mod` | `go build ./...` |
| `rust` | `Cargo.toml` | `cargo build` |
| `maven` | `pom.xml` | `./mvnw -B -DskipTests package` (or `mvn`) |
| `gradle` | `build.gradle`, `build.gradle.kts`, `settings.gradle`, `settings.gradle.kts` | `./gradlew assemble` (or `gradle`) |
| `dotnet` | `*.sln` or `*.csproj` | `dotnet build` |
| `ruby` | `Gemfile` | `bundle install` |
| `elixir` | `mix.exs` | `mix deps.get && mix compile` |
| `bazel` | `MODULE.bazel`, `WORKSPACE`, `WORKSPACE.bazel` | `bazel build //...` |
| `docker` | `Dockerfile` | `docker build .` |

Toolchains come from the GitHub-hosted runner image, which carries Node, Python, Go, Rust,
Java, .NET, Ruby, Bazelisk and Docker. Three things are installed on top when the files
are present: `jdx/mise-action` for `mise.toml` or `.tool-versions`, `actions/setup-node`
for `.nvmrc` or `.node-version`, and `actions/setup-python` for `.python-version`. Elixir
and Bun are detected but not installed; use `mise` or a `build_command` for those.

Compile-time checks that are not builds (`tsc --noEmit`, `mypy`, `go vet`) are not run.
They are CI's job, and the build already reports what they would.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `build_command` | _(none)_ | Build command. When set, detection is skipped. |
| `working_directory` | `.` | Directory, relative to the workspace, to detect and build in. |
| `timeout_minutes` | `20` | Minutes allowed for the build command. On the reusable workflow this input is called `build_timeout_minutes`. |
| `fail_on` | `FAIL` | `FAIL`, or `none` to stay advisory. |
| `python_version` | `3.11` | Python used to run the probe. |

The reusable workflow adds `runs_on` (default `ubuntu-latest`) and a job `timeout_minutes`
(default `25`); keep `build_timeout_minutes` under it so the status step still runs.

Outputs: `status` (`PASS`, `FAIL`, `SKIPPED`), `detected` (the row name above, or empty)
and `build_command` (what ran).

## Merge queues

The workflow runs on `merge_group` and builds `github.sha`, the merge result, so a pull
request that was green alone is re-verified against what it will actually land on.
