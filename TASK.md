# Automated QA Testing:

Validate pull request changes by actually running the software — not just reading code or running tests.

Automated QA testing goes beyond code review and CI: instead of reading diffs or running the test suite, the QA agent actually **runs the software** and verifies that changes work as claimed. It sets up the environment, exercises changed behavior as a real user would (browser, CLI, API requests), and posts a structured report with evidence.

## Overview

The QA agent follows a four-phase methodology:

1. **Understand** — Reads the PR diff, title, and description. Classifies changes (new feature, bug fix, refactor, config) and identifies entry points (CLI commands, API endpoints, UI pages).
2. **Setup** — Bootstraps the repository: installs dependencies, builds the project, notes CI status.
3. **Exercise** — The core phase: spins up servers, opens browsers, runs CLI commands, makes HTTP requests — testing the changed behavior as a real user would. For bug fixes, it reproduces the bug on the base branch and verifies the fix on the PR branch.
4. **Report** — Posts a structured QA report as a PR comment, with evidence (commands run, outputs, screenshots) and a verdict (PASS / FAIL / PARTIAL).

The QA agent knows when to give up: after exhausting multiple approaches without progress, it reports what it tried and stops — rather than spinning endlessly.

## What It Does (and Doesn't)

**The QA agent does:**

- Run the actual application and interact with it
- Make real HTTP requests, run real CLI commands
- Open browsers and verify UI changes
- Reproduce bugs and verify fixes end-to-end
- Report with evidence (commands, outputs, screenshots)

**The QA agent does NOT:**

- Run the test suite (that's CI's job)
- Analyze code for style or structure (that's code review's job)
- Run linters, formatters, or type checkers
- Substitute `--help` or `--dry-run` for real execution

## Quick Start

### GitHub Actions

Create a workflow into `.github/workflows/qa-changes.yml` in repository and add `LLM_API_KEY` to **Settings → Secrets and variables → Actions** (User task). Create `action.yml` in the plugin for all available inputs.

## QA Report Format

The QA agent posts a structured report as a PR comment:

```
## QA Report

**Status: PASS**

### Changes Tested
- New `/api/health` endpoint returns 200 with version info
- Dashboard page renders at `/dashboard` with correct data

### Evidence
1. Started server with `npm run dev`
2. `curl http://localhost:3000/api/health` → 200 OK, body: {"status":"ok","version":"1.2.0"}
3. Navigated to http://localhost:3000/dashboard — page renders correctly
   [screenshot attached]

### Edge Cases
- Empty database state: dashboard shows "No data" placeholder
- Invalid auth token: returns 401 as expected
```

## Customization

### Change Types

The QA agent adapts its approach based on the type of change:

| Change Type       | QA Approach                                                                   |
| ----------------- | ----------------------------------------------------------------------------- |
| **Frontend / UI** | Starts dev server, opens browser, verifies visual changes, tests interactions |
| **CLI**           | Runs commands with realistic arguments, verifies output, tests edge cases     |
| **API / Backend** | Starts server, makes HTTP requests, verifies responses and side effects       |
| **Bug fix**       | Reproduces bug on base branch, verifies fix on PR branch (before/after)       |
| **Library / SDK** | Writes and runs a short script that imports and calls changed functions       |

### Repository-Specific QA Guidelines

Add repo-specific QA instructions by creating `.agents/skills/qa-guide.md`:

```markdown
---
name: qa-guide
description: Project-specific QA guidelines
triggers:
- /qa-changes
---

# QA Guidelines for [Your Project]

## Environment Setup
- Run `make setup` to initialize the development environment
- The dev server runs on port 8080

## Key Test Scenarios
- Always verify the admin dashboard at /admin after backend changes
- For API changes, test with both authenticated and unauthenticated requests

## Known Limitations
- The payment module requires a Stripe test key — skip payment flow testing
```

## Troubleshooting

**QA agent can't start the server**

Ensure your repository's setup instructions are documented in `README.md` or `AGENTS.md`. The agent follows these to bootstrap the environment. If setup requires special steps, add them to a custom QA guide.

**QA report says PARTIAL**

PARTIAL means some scenarios passed and others failed or couldn't be tested. Read the report details — it will explain what worked and what didn't. Common causes: missing environment variables, external service dependencies, or insufficient permissions.

**QA takes too long**

For large PRs with many changed entry points, the agent may need more time. Consider splitting large PRs into smaller, focused changes. You can also add a custom QA guide that prioritizes the most important scenarios.

## Automate This

### GitHub Action: (Per-Repo)

Use the `qa-changes` plugin as a GitHub Actions workflow. Copy the example workflow into `.github/workflows/qa-changes.yml` in your repository, add your `LLM_API_KEY` to **Settings → Secrets and variables → Actions**, and customize the trigger conditions and model as needed.

See `action.yml` for all available inputs.

**When to use this:** You want per-repo control, need to integrate with existing CI checks, or want to pin specific action versions per repository.
