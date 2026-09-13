"""Lint an API spec, diff it against the base branch, and fuzz a running server with it."""

import os
import re
import shutil
from dataclasses import dataclass

from qa_agent import tools

from . import common, server

SPEC_CANDIDATES = (
    "openapi.yaml",
    "openapi.yml",
    "openapi.json",
    "swagger.yaml",
    "swagger.yml",
    "swagger.json",
    "schema.graphql",
    "schema.graphqls",
)
GRAPHQL_SUFFIXES = (".graphql", ".graphqls", ".gql")
RULESETS = (".vacuum.yaml", ".vacuum.yml", ".spectral.yaml", ".spectral.yml")
GRAPHQL_INSPECTOR = "npx -y -p graphql@16.14.2 -p @graphql-inspector/cli@7.0.0 graphql-inspector"


@dataclass(frozen=True)
class Config:
    workspace: str
    spec_path: str
    base_ref: str
    lint: bool
    breaking: bool
    base_url: str
    start_command: str
    compose_file: str
    ready_url: str
    ready_timeout: int
    auth_header: str
    max_examples: int
    hurl_dir: str
    setup_command: str
    command_timeout: int
    artifacts_dir: str
    fail_on: tuple


def detect_spec(workspace):
    for name in SPEC_CANDIDATES:
        if os.path.isfile(os.path.join(workspace, name)):
            return name
    return ""


def kind_of(spec_path):
    return "graphql" if spec_path.lower().endswith(GRAPHQL_SUFFIXES) else "openapi"


def from_env():
    workspace = common.env("QA_WORKSPACE", os.getcwd())
    base_url = common.env("QA_API_BASE_URL")
    return Config(
        workspace=workspace,
        spec_path=common.env("QA_API_SPEC_PATH") or detect_spec(workspace),
        base_ref=_base_ref(),
        lint=common.bool_env("QA_API_LINT"),
        breaking=common.bool_env("QA_API_BREAKING"),
        base_url=base_url,
        start_command=common.env("QA_API_START_COMMAND"),
        compose_file=common.env("QA_API_COMPOSE_FILE"),
        ready_url=common.env("QA_API_READY_URL") or base_url,
        ready_timeout=common.int_env("QA_API_READY_TIMEOUT", "120"),
        auth_header=common.env("QA_API_AUTH_HEADER"),
        max_examples=common.int_env("QA_API_MAX_EXAMPLES", "50"),
        hurl_dir=common.env("QA_API_HURL_DIR"),
        setup_command=common.env("QA_API_SETUP_COMMAND"),
        command_timeout=common.int_env("QA_API_COMMAND_TIMEOUT", "600"),
        artifacts_dir=common.env("QA_ARTIFACTS_DIR"),
        fail_on=common.parse_fail_on(common.env("QA_API_FAIL_ON", "FAIL")),
    )


def _base_ref():
    """The git ref holding the base spec: an explicit input, else the PR base branch."""
    explicit = common.env("QA_API_BASE_REF")
    if explicit:
        branch = explicit.replace("refs/heads/", "", 1)
        if branch.startswith(("origin/", "HEAD")) or re.fullmatch(r"[0-9a-f]{7,40}", branch):
            return branch
        return "origin/" + branch
    branch = common.env("GITHUB_BASE_REF")
    return "origin/" + branch if branch else ""


def main():
    config = from_env()
    if not config.spec_path:
        common.summarize(
            "Skipping API contract: no spec found. Set `spec_path`, or add one of %s at the "
            "repository root." % ", ".join("`%s`" % name for name in SPEC_CANDIDATES)
        )
        return common.finish("SKIPPED", config.fail_on)
    if not os.path.isfile(os.path.join(config.workspace, config.spec_path)):
        raise common.ConfigError("spec_path %r does not exist" % config.spec_path)
    if config.artifacts_dir:
        os.makedirs(config.artifacts_dir, exist_ok=True)

    kind = kind_of(config.spec_path)
    rows = []
    if config.lint:
        rows.append(_lint(config, kind))
    if config.breaking:
        rows.append(_breaking(config, kind))
    if config.base_url or config.start_command or config.compose_file:
        rows += _live(config, kind)

    status = common.verdict(rows)
    common.summarize(common.table("API contract (%s): %s" % (config.spec_path, status), rows))
    common.log_rows(rows)
    common.set_output("spec_path", config.spec_path)
    common.set_output("kind", kind)
    return common.finish(status, config.fail_on)


def _lint(config, kind):
    if kind != "openapi":
        return ("lint", None, "no linter for GraphQL schemas")
    if not shutil.which("vacuum"):
        return ("lint", None, "vacuum is not installed")
    ruleset = next(
        (name for name in RULESETS if os.path.isfile(os.path.join(config.workspace, name))), ""
    )
    command_line = "vacuum lint --fail-severity error --no-style --details%s %s" % (
        " -r %s" % ruleset if ruleset else "",
        config.spec_path,
    )
    return server.command("lint", command_line, config)


def _breaking(config, kind):
    if not config.base_ref:
        return ("breaking", None, "no base ref to compare against")
    base_spec = _base_spec(config)
    if base_spec is None:
        return (
            "breaking",
            None,
            "%s has no %s; nothing to compare" % (config.base_ref, config.spec_path),
        )
    if kind == "graphql":
        command_line = "%s diff %s %s" % (GRAPHQL_INSPECTOR, base_spec, config.spec_path)
    else:
        if not shutil.which("oasdiff"):
            return ("breaking", None, "oasdiff is not installed")
        command_line = "oasdiff breaking --fail-on ERR %s %s" % (base_spec, config.spec_path)
    return server.command("breaking", command_line, config)


def _base_spec(config):
    """Write the base branch's copy of the spec to a temp file; None when it has none."""
    ref = config.base_ref
    if tools.execute("git rev-parse --verify --quiet %s" % ref, config.workspace, 30)[0] != 0:
        branch = ref.replace("origin/", "", 1)
        tools.execute(
            "git fetch --no-tags --depth=1 origin %s:refs/remotes/origin/%s" % (branch, branch),
            config.workspace,
            120,
        )
    target = os.path.join(
        config.artifacts_dir or config.workspace, "base-" + os.path.basename(config.spec_path)
    )
    code, _ = tools.execute(
        "git show %s:%s > %s" % (ref, config.spec_path, target), config.workspace, 30
    )
    return target if code == 0 else None


def _live(config, kind):
    rows = []
    running = None
    try:
        if config.setup_command:
            rows.append(server.command("setup", config.setup_command, config))
        if common.healthy(rows):
            running = server.start(config, rows)
            if running not in (None, False):
                rows.append(server.wait_ready(config, running))
        if common.healthy(rows):
            rows.append(_schemathesis(config, kind))
            if config.hurl_dir:
                rows.append(server.hurl(config, _api_url(config)))
    finally:
        server.stop(config, running)
    return rows


def _api_url(config):
    """Where the API lives: base_url, else the origin of the ready URL (which may be /health)."""
    return (config.base_url or server.origin(config.ready_url)).rstrip("/")


def _schemathesis(config, kind):
    if not shutil.which("schemathesis"):
        return ("conformance", None, "schemathesis is not installed")
    target = _api_url(config)
    report_dir = os.path.join(config.artifacts_dir or config.workspace, "schemathesis")
    schema = target if kind == "graphql" else config.spec_path
    command_line = (
        "schemathesis run %s --url %s --max-examples %d --checks all "
        "--report junit --report-dir %s%s"
    ) % (
        schema,
        target,
        config.max_examples,
        report_dir,
        ' -H "%s"' % config.auth_header if config.auth_header else "",
    )
    return server.command("conformance", command_line, config)


if __name__ == "__main__":
    common.run(main)
