# Preview Smoke

The [Smoke](smoke.md) route and command checks, run against a deployment's preview URL
once the deployment platform reports it live. Nothing is started; the platform (Vercel,
Netlify, Cloudflare Pages, Render, Railway, Fly and others) already did that and told GitHub
through the Deployments API.

## Quick start

```yaml
name: Preview Smoke

on:
  deployment_status:

jobs:
  smoke:
    permissions:
      contents: read
    uses: khawjaahmad/github-workflows/.github/workflows/preview-smoke.yml@v1
    with:
      environment: Preview
      routes: |
        /
        /api/health
        /login=200
```

The caller owns the trigger: `deployment_status` fires for every state change of every
deployment, and the workflow's job runs only when the state is `success` and, if
`environment` is set, the deployment targets that environment. Other events are skipped.

## What it does

Reads `environment_url` (or `target_url`) from the event, polls it until it answers, then
checks each route and runs each command exactly as Smoke does. Commands see the URL as
`PREVIEW_URL`. The results table and verdict are Smoke's.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `routes` | `/` | Newline-separated `path[=expected_status]` entries. |
| `commands` | _(none)_ | Commands that must exit 0; `PREVIEW_URL` is set. |
| `environment` | _(any)_ | Only react to deployments to this environment. |
| `ready_timeout` | `120` | Seconds to wait for the preview to answer. |
| `fail_on` | `FAIL` | `FAIL`, or `none` to stay advisory. |
| `runs_on` | `ubuntu-latest` | Runner label. |

Outputs: `status` and `url`.

## Notes

- A `deployment_status` run is not attached to a pull request check by default; the
  platform's own deployment status is. Use `fail_on: none` unless the platform posts the
  deployment as a required check.
- For the same routes on a locally started server, use Smoke on `pull_request`.
