# Web Check

Loads every route of a site in a real browser and reports what an end-to-end suite written
for the happy path does not: JavaScript errors, failed requests, 4xx and 5xx responses,
serious accessibility violations, broken links and Lighthouse scores under a budget.

The only thing it needs is a URL. Give it a preview deployment, or a start command and a
port and it starts the site itself.

## Quick start

```yaml
name: Web Check

on:
  pull_request:
  merge_group:

jobs:
  web:
    permissions:
      contents: read
    uses: khawjaahmad/github-workflows/.github/workflows/web-check.yml@v1
    with:
      setup_command: npm ci
      start_command: npm run dev
      port: 3000
```

Against a preview URL from a deployment platform:

```yaml
on:
  deployment_status:

jobs:
  web:
    if: github.event.deployment_status.state == 'success'
    uses: khawjaahmad/github-workflows/.github/workflows/web-check.yml@v1
    with:
      url: ${{ github.event.deployment_status.environment_url }}
```

## What it checks

Routes come from the `routes` input, else from `/sitemap.xml`, else from the same-origin
links on the front page, capped at `max_routes`. Then, per route:

| Angle | Fails when | Artifact |
| --- | --- | --- |
| route | The page returns 4xx or 5xx, throws, logs a console error, or any request fails or answers 4xx or 5xx (favicon requests excluded). | screenshot |
| a11y | axe reports a serious or critical violation. Minor and moderate findings are counted but do not fail. | `<route>-axe.json` |

And once per site:

| Angle | Fails when | Artifact |
| --- | --- | --- |
| links | linkinator's recursive crawl finds a broken link. | `links.json` |
| lighthouse | Any category score on the first `lighthouse_routes` routes is under `lighthouse_budget`. | `<route>-lighthouse.html` |

Every angle is a switch. The verdict is `PASS` when every check passed, `FAIL` otherwise,
`SKIPPED` when no URL or port was given.

The browser is the runner's Google Chrome, which `ubuntu-latest` ships, so no browser is
downloaded. Playwright, axe, linkinator and Lighthouse are installed from the pinned
`package-lock.json` in the action directory.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `url` | _(none)_ | A running site. Exclusive with `start_command`. |
| `start_command` / `compose_file` | _(none)_ | Start the site here; needs `port`. |
| `port` | _(none)_ | Port the started site answers on at 127.0.0.1. |
| `ready_timeout` | `120` | Seconds to wait for the site. |
| `routes` | discovered | Newline-separated paths. |
| `max_routes` | `25` | Cap on discovered routes. |
| `console_errors` | `true` | The route angle. |
| `links` | `true` | The links angle. |
| `accessibility` | `true` | The a11y angle. |
| `lighthouse` | `true` | The Lighthouse angle. |
| `lighthouse_budget` | `80` | Minimum score per category. |
| `lighthouse_routes` | `3` | Routes Lighthouse runs on. Each takes 20 to 40 seconds. |
| `setup_command` | _(none)_ | Runs before the site starts. |
| `command_timeout` | `900` | Seconds for the whole browser run. |
| `fail_on` | `FAIL` | `FAIL`, or `none` to stay advisory. |

Outputs: `status` and `url`.

## Runtime

The route sweep, accessibility and links finish in one to two minutes on a small site.
Lighthouse adds half a minute per route; lower `lighthouse_routes` or turn it off on large
sites, or run it on a schedule instead of every pull request.

Not included, and listed in the research report as opt-in for later: HTML validation,
security-header and passive DAST scans, and visual regression.
