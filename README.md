# github-workflows

Shared CI building blocks — composite actions and reusable workflows — that other repositories
call instead of copying YAML into each one. A fix here reaches every consumer without touching
a single consumer file.

Everything in this repository is MIT licensed and safe to depend on from a public or private
repository.

## What's here

| Name | Kind | Reference | Docs |
| ---- | ---- | --------- | ---- |
| **QA Changes** | Composite action | `khawjaahmad/github-workflows@v1` | [Reference](docs/qa-changes.md) · [Rollout guide](USAGE.md) |
| **QA Changes** | Reusable workflow | `khawjaahmad/github-workflows/.github/workflows/qa.yml@v1` | [Rollout guide](USAGE.md) |

**QA Changes** validates a pull request by *running the software* rather than reading the
diff. It sets up the repository, exercises the changed behaviour as a real user would — CLI,
HTTP, browser — and posts a structured report with evidence and a verdict of PASS, FAIL or
PARTIAL. It is not a code reviewer and not a test runner; those jobs already have owners.

## Using any of this

Most repositories want the reusable workflow, which keeps the consumer file down to about ten
lines:

```yaml
name: QA Changes

on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  qa:
    permissions:
      contents: read
      pull-requests: write
    uses: khawjaahmad/github-workflows/.github/workflows/qa.yml@v1
    secrets: inherit
    with:
      model: ${{ vars.LLM_MODEL }}
```

[USAGE.md](USAGE.md) covers the rest: configuring the provider once for a whole organization,
the fork-safe preflight, and rolling the file out to many repositories at once.

## Conventions

These hold across everything in this repository, so a repository configured once works with
whatever gets added later.

- **Provider credentials** live in two places and move together: `LLM_API_KEY` as an
  organization secret, `LLM_MODEL` as an organization variable. No model id is ever defaulted
  in code — model ids change faster than this repository does, and a default here would
  quietly pin every consumer to a superseded model.
- **Pin to `@v1`.** The tag moves with each compatible release, so you get fixes without
  tracking an unreviewed `main`. Pin to an exact tag such as `@v1.0.0` if you would rather
  review every bump yourself.
- **Fork pull requests get no secrets**, so anything needing a provider key skips with an
  explanation on the job summary rather than putting a red check on a contributor's first
  pull request. Nothing here uses `pull_request_target`, which would run fork code with your
  secrets.

## Versions

`v1` is a moving tag pointing at the newest compatible release; `v1.0.0` and its successors
are fixed. Both currently resolve to the same commit.

Note for maintainers: the reusable workflow in `.github/workflows/qa.yml` cannot refer to the
action with `./`, because inside a reusable workflow that resolves to the *caller's* checkout.
It carries a pinned reference instead, which has to be bumped in the release commit, before
tagging, so the tag and the code it runs are the same commit.

## Layout

```
action.yml                       The QA Changes composite action
qa_agent/                        Its implementation — standard library only
.github/workflows/qa.yml         The QA Changes reusable workflow
.github/workflows/qa-changes.yml This repository QA'ing its own pull requests
.github/workflows/test.yml       The unit tests
docs/                            Per-workflow reference documentation
USAGE.md                         Rolling QA Changes out across repositories
tests/                           The unit tests — no network and no API key needed
```

`action.yml` sits at the repository root, which is what makes
`khawjaahmad/github-workflows@v1` resolve to QA Changes. A second composite action would live
in a subdirectory and be referenced as `khawjaahmad/github-workflows/<name>@v1`; additional
reusable workflows just need another file in `.github/workflows/`.

## Development

```bash
python -m unittest discover -s tests -v
```

The tests run the whole action — configuration, agent loop, tools and comment posting — against
stub OpenAI-compatible and GitHub servers. No API key and no network needed.

## License

[MIT](LICENSE).
