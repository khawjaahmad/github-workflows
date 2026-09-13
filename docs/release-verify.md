# Release Verify

Release hygiene, off the merge critical path: a Conventional Commits title on every pull
request, an SBOM of the tree, and signed build provenance for release artifacts. Three
independent jobs; each runs only when its input asks for it.

## Quick start

```yaml
name: Release Verify

on:
  pull_request:
    types: [opened, edited, synchronize, reopened]
  push:
    tags: ["v*"]

jobs:
  verify:
    permissions:
      contents: read
      pull-requests: read
      id-token: write
      attestations: write
    uses: khawjaahmad/github-workflows/.github/workflows/release-verify.yml@v1
    with:
      sbom: true
      attest_paths: dist/*
      artifact_name: dist
```

## Jobs

| Job | Runs when | What it does |
| --- | --- | --- |
| `title` | `pr_title` is true and the event is `pull_request` | `amannn/action-semantic-pull-request` checks the title is `type(scope): subject` with an allowed type. Include `edited` in the caller's trigger types so a fixed title re-runs it. |
| `sbom` | `sbom` is true | `anchore/sbom-action` writes an SPDX JSON SBOM of the checkout and uploads it as a workflow artifact named `sbom-<sha>.spdx.json`. |
| `attest` | `attest_paths` is set | Downloads `artifact_name` into `release-artifacts/` when given, then `actions/attest-build-provenance` signs a provenance attestation for each path. Needs `id-token: write` and `attestations: write` in the caller. Verify later with `gh attestation verify`. |

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `pr_title` | `true` | Require a Conventional Commits title. |
| `title_types` | Conventional defaults | Allowed types, one per line. |
| `sbom` | `false` | Generate and upload an SBOM. |
| `attest_paths` | _(none)_ | Paths to attest, one per line. |
| `artifact_name` | _(none)_ | Artifact to download before attesting. |
| `runs_on` | `ubuntu-latest` | Runner label. |

All three actions are pinned by commit: `amannn/action-semantic-pull-request` 6.1.1 (MIT),
`anchore/sbom-action` 0.24.2 (Apache-2.0), `actions/attest-build-provenance` 4.2.2 (MIT).
Attestations on private repositories need GitHub Enterprise Cloud.
