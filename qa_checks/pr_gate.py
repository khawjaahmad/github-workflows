"""Deterministic hygiene checks on a pull request's metadata and diff."""

import fnmatch
import re
from dataclasses import dataclass

from qa_agent import github
from qa_agent.endpoints import GITHUB_API_ROOT

from . import common

DEFAULT_IGNORE = "*.lock,package-lock.json,yarn.lock,pnpm-lock.yaml,go.sum,*.snap,*.min.js"
DEFAULT_TEST_GLOBS = "test*,*/test*,*_test.*,*.test.*,*.spec.*,*/spec/*,*/__tests__/*"
DEFAULT_AGENT_LOGINS = "copilot-swe-agent[bot],devin-ai-integration[bot]"
AGENT_TRAILER = re.compile(
    r"^co-authored-by:.*\b(claude|codex|copilot|cursor|devin|gemini)\b", re.IGNORECASE | re.M
)
TODO = re.compile(r"\b(TODO|FIXME)\b")
DIFF_LIMIT = 10_000_000


@dataclass(frozen=True)
class Config:
    token: str
    repo: str
    pr_number: int
    api_root: str
    max_lines: int
    ignore_globs: tuple
    allowed_paths: tuple
    forbidden_paths: tuple
    no_new_todos: bool
    require_tests_for: tuple
    test_globs: tuple
    label_agent_prs: bool
    agent_logins: tuple
    agent_label: str
    dependency_review: bool


def from_env():
    pr_number = common.env("QA_PR_NUMBER")
    if not pr_number.isdigit():
        raise common.ConfigError("QA_PR_NUMBER is not a number: %r" % pr_number)
    repo = common.env("QA_REPO")
    if not repo:
        raise common.ConfigError("QA_REPO is empty (expected 'owner/name')")
    return Config(
        token=common.env("QA_GITHUB_TOKEN"),
        repo=repo,
        pr_number=int(pr_number),
        api_root=common.env("QA_GITHUB_API_URL", GITHUB_API_ROOT).rstrip("/"),
        max_lines=common.int_env("QA_GATE_MAX_LINES", "1500"),
        ignore_globs=tuple(common.globs(common.env("QA_GATE_IGNORE_GLOBS", DEFAULT_IGNORE))),
        allowed_paths=tuple(common.globs(common.env("QA_GATE_ALLOWED_PATHS"))),
        forbidden_paths=tuple(common.globs(common.env("QA_GATE_FORBIDDEN_PATHS"))),
        no_new_todos=common.bool_env("QA_GATE_NO_NEW_TODOS"),
        require_tests_for=tuple(common.globs(common.env("QA_GATE_REQUIRE_TESTS_FOR"))),
        test_globs=tuple(common.globs(common.env("QA_GATE_TEST_GLOBS", DEFAULT_TEST_GLOBS))),
        label_agent_prs=common.bool_env("QA_GATE_LABEL_AGENT_PRS"),
        agent_logins=tuple(
            login.lower()
            for login in common.globs(common.env("QA_GATE_AGENT_LOGINS", DEFAULT_AGENT_LOGINS))
        ),
        agent_label=common.env("QA_GATE_AGENT_LABEL", "ai-generated"),
        dependency_review=common.bool_env("QA_GATE_DEPENDENCY_REVIEW", "false"),
    )


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def check_size(files, config):
    counted = [f for f in files if not matches(f["filename"], config.ignore_globs)]
    total = sum(f.get("additions", 0) + f.get("deletions", 0) for f in counted)
    if config.max_lines <= 0:
        return ("size", None, "%d lines changed (no limit)" % total)
    return (
        "size",
        total <= config.max_lines,
        "%d lines changed, limit %d" % (total, config.max_lines),
    )


def check_paths(files, config):
    if not (config.allowed_paths or config.forbidden_paths):
        return ("paths", None, "no path rules")
    outside = [
        f["filename"]
        for f in files
        if (config.allowed_paths and not matches(f["filename"], config.allowed_paths))
        or matches(f["filename"], config.forbidden_paths)
    ]
    if outside:
        return ("paths", False, "outside the allowed paths: %s" % ", ".join(outside[:10]))
    return ("paths", True, "%d files, all within the allowed paths" % len(files))


def check_todos(diff, config):
    if not config.no_new_todos:
        return ("todos", None, "check disabled")
    added = [
        line[1:].strip()
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++") and TODO.search(line)
    ]
    if added:
        return (
            "todos",
            False,
            "%d added line(s) with TODO/FIXME, e.g. `%s`" % (len(added), added[0][:120]),
        )
    return ("todos", True, "no TODO or FIXME added")


def check_tests(files, config):
    if not config.require_tests_for:
        return ("tests", None, "no source globs given")
    names = [f["filename"] for f in files]
    touched = [name for name in names if matches(name, config.require_tests_for)]
    if not touched:
        return ("tests", True, "no matching source files changed")
    tests = [name for name in names if matches(name, config.test_globs)]
    if tests:
        return (
            "tests",
            True,
            "%d test file(s) changed alongside %d source file(s)" % (len(tests), len(touched)),
        )
    return (
        "tests",
        False,
        "%d source file(s) changed with no test change: %s"
        % (len(touched), ", ".join(touched[:5])),
    )


def is_agent_pr(pull_request, commits, config):
    author = ((pull_request.get("user") or {}).get("login") or "").lower()
    if author in config.agent_logins:
        return True
    return any(AGENT_TRAILER.search((c.get("commit") or {}).get("message") or "") for c in commits)


def main():
    config = from_env()
    call = dict(api_root=config.api_root)
    pull_request = github.get_pull_request(config.token, config.repo, config.pr_number, **call)
    files = github.list_files(config.token, config.repo, config.pr_number, **call)
    diff = github.get_diff(
        config.token, config.repo, config.pr_number, max_chars=DIFF_LIMIT, **call
    )
    commits = github.list_commits(config.token, config.repo, config.pr_number, **call)

    rows = [
        check_size(files, config),
        check_paths(files, config),
        check_todos(diff, config),
        check_tests(files, config),
    ]

    agent = is_agent_pr(pull_request, commits, config)
    if agent and config.label_agent_prs:
        try:
            github.add_labels(
                config.token, config.repo, config.pr_number, [config.agent_label], **call
            )
            rows.append(("agent label", True, "labelled `%s`" % config.agent_label))
        except github.GitHubError as error:
            rows.append(("agent label", None, "could not label (%s)" % str(error)[:120]))

    graph = False
    if config.dependency_review:
        base = (pull_request.get("base") or {}).get("sha") or ""
        head = (pull_request.get("head") or {}).get("sha") or ""
        graph = github.dependency_graph_enabled(config.token, config.repo, base, head, **call)
        if not graph:
            rows.append(
                ("dependency review", None, "dependency graph is not enabled on this repository")
            )

    status = common.verdict(rows)
    common.summarize(common.table("PR gate: %s" % status, rows))
    for name, ok, detail in rows:
        common.log("[%s] %s: %s" % ({True: "ok", False: "FAIL", None: "skip"}[ok], name, detail))
    common.set_output("is_agent_pr", str(agent).lower())
    common.set_output("dependency_graph", str(graph).lower())
    common.set_output(
        "workflows_changed", str(any(f["filename"].startswith(".github/") for f in files)).lower()
    )
    common.set_output("status", status)
    common.log("status: %s" % status)
    return 0


if __name__ == "__main__":
    common.run(main)
