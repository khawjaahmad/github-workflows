"""Start the site (or take a URL), then run the browser checks in web-check/check.mjs."""

import json
import os
from dataclasses import dataclass

from qa_agent import tools

from . import common, server

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web-check")


@dataclass(frozen=True)
class Config:
    workspace: str
    url: str
    start_command: str
    compose_file: str
    port: int
    ready_url: str
    ready_timeout: int
    routes: tuple
    max_routes: int
    console_errors: bool
    links: bool
    accessibility: bool
    lighthouse: bool
    lighthouse_budget: int
    lighthouse_routes: int
    setup_command: str
    command_timeout: int
    artifacts_dir: str
    script_dir: str
    fail_on: tuple


def from_env():
    url = common.env("QA_WEB_URL")
    port = common.int_env("QA_WEB_PORT", "0")
    ready_url = url or ("http://127.0.0.1:%d/" % port if port else "")
    config = Config(
        workspace=common.env("QA_WORKSPACE", os.getcwd()),
        url=url,
        start_command=common.env("QA_WEB_START_COMMAND"),
        compose_file=common.env("QA_WEB_COMPOSE_FILE"),
        port=port,
        ready_url=ready_url,
        ready_timeout=common.int_env("QA_WEB_READY_TIMEOUT", "120"),
        routes=tuple(common.lines(common.env("QA_WEB_ROUTES"))),
        max_routes=common.int_env("QA_WEB_MAX_ROUTES", "25"),
        console_errors=common.bool_env("QA_WEB_CONSOLE_ERRORS"),
        links=common.bool_env("QA_WEB_LINKS"),
        accessibility=common.bool_env("QA_WEB_ACCESSIBILITY"),
        lighthouse=common.bool_env("QA_WEB_LIGHTHOUSE"),
        lighthouse_budget=common.int_env("QA_WEB_LIGHTHOUSE_BUDGET", "80"),
        lighthouse_routes=common.int_env("QA_WEB_LIGHTHOUSE_ROUTES", "3"),
        setup_command=common.env("QA_WEB_SETUP_COMMAND"),
        command_timeout=common.int_env("QA_WEB_COMMAND_TIMEOUT", "900"),
        artifacts_dir=common.env("QA_ARTIFACTS_DIR"),
        script_dir=common.env("QA_WEB_SCRIPT_DIR", SCRIPT),
        fail_on=common.parse_fail_on(common.env("QA_WEB_FAIL_ON", "FAIL")),
    )
    if config.start_command and config.compose_file:
        raise common.ConfigError("set start_command or compose_file, not both")
    if (config.start_command or config.compose_file) and not config.ready_url:
        raise common.ConfigError("start_command and compose_file need port (or url) to poll")
    return config


def main():
    config = from_env()
    if not config.ready_url:
        common.summarize("Skipping web check: set `url`, or `start_command` plus `port`.")
        return common.finish("SKIPPED", config.fail_on)
    artifacts = config.artifacts_dir or os.path.join(config.workspace, "web-check-artifacts")
    os.makedirs(artifacts, exist_ok=True)

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
            rows += _browser_checks(config, artifacts)
    finally:
        server.stop(config, running)

    status = common.verdict(rows)
    common.summarize(common.table("Web check (%s): %s" % (config.ready_url, status), rows))
    common.log_rows(rows)
    common.set_output("url", config.ready_url)
    return common.finish(status, config.fail_on)


def _browser_checks(config, artifacts):
    """Run check.mjs with a JSON config; it answers with JSON rows."""
    request = os.path.join(artifacts, "web-check-config.json")
    response = os.path.join(artifacts, "web-check-results.json")
    with open(request, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "url": config.ready_url,
                "routes": list(config.routes),
                "maxRoutes": config.max_routes,
                "consoleErrors": config.console_errors,
                "links": config.links,
                "accessibility": config.accessibility,
                "lighthouse": config.lighthouse,
                "lighthouseBudget": config.lighthouse_budget,
                "lighthouseRoutes": config.lighthouse_routes,
                "artifacts": artifacts,
            },
            handle,
        )
    script = os.path.join(config.script_dir, "check.mjs")
    code, output = tools.execute(
        'node "%s" "%s" "%s"' % (script, request, response),
        config.workspace,
        config.command_timeout,
    )
    log_path = os.path.join(artifacts, "web-check.log")
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(output)
    try:
        with open(response, encoding="utf-8") as handle:
            rows = [tuple(row) for row in json.load(handle)["rows"]]
    except (OSError, ValueError, KeyError, TypeError):
        rows = []
    if not rows or code != 0:
        rows.append(
            (
                "browser checks",
                False,
                "check.mjs exit %s: %s (see web-check.log)" % (code, server.tail(output)),
            )
        )
    return rows


if __name__ == "__main__":
    common.run(main)
