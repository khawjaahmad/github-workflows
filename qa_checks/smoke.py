"""Boot the software, wait until it answers, hit its routes and commands."""

import os
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from . import common, server


@dataclass(frozen=True)
class Config:
    workspace: str
    start_command: str
    compose_file: str
    ready_url: str
    ready_timeout: int
    routes: tuple
    commands: tuple
    hurl_dir: str
    setup_command: str
    command_timeout: int
    artifacts_dir: str
    fail_on: tuple


def from_env():
    config = Config(
        workspace=common.env("QA_WORKSPACE", os.getcwd()),
        start_command=common.env("QA_SMOKE_START_COMMAND"),
        compose_file=common.env("QA_SMOKE_COMPOSE_FILE"),
        ready_url=common.env("QA_SMOKE_READY_URL"),
        ready_timeout=common.int_env("QA_SMOKE_READY_TIMEOUT", "120"),
        routes=tuple(common.lines(common.env("QA_SMOKE_ROUTES"))),
        commands=tuple(common.lines(common.env("QA_SMOKE_COMMANDS"))),
        hurl_dir=common.env("QA_SMOKE_HURL_DIR"),
        setup_command=common.env("QA_SMOKE_SETUP_COMMAND"),
        command_timeout=common.int_env("QA_SMOKE_COMMAND_TIMEOUT", "300"),
        artifacts_dir=common.env("QA_ARTIFACTS_DIR"),
        fail_on=common.parse_fail_on(common.env("QA_SMOKE_FAIL_ON", "FAIL")),
    )
    if config.start_command and config.compose_file:
        raise common.ConfigError("set start_command or compose_file, not both")
    if (config.routes or config.hurl_dir) and not config.ready_url:
        raise common.ConfigError("routes and hurl_dir need ready_url to know where the server is")
    return config


def main():
    config = from_env()
    if not (config.start_command or config.compose_file or config.routes or config.commands):
        common.summarize(
            "Skipping smoke: nothing to start or check (set `start_command`, "
            "`compose_file`, `routes` or `commands`)."
        )
        return common.finish("SKIPPED", config.fail_on)

    if config.artifacts_dir:
        os.makedirs(config.artifacts_dir, exist_ok=True)

    rows = []
    running = None
    try:
        if config.setup_command:
            rows.append(server.command("setup", config.setup_command, config))
        if not rows or rows[-1][1]:
            running = server.start(config, rows)
            if running is not False and config.ready_url:
                rows.append(server.wait_ready(config, running))
            if common.healthy(rows):
                rows += [_route(config, spec) for spec in config.routes]
                rows += [server.command("command", line, config) for line in config.commands]
                if config.hurl_dir:
                    rows.append(server.hurl(config))
    finally:
        server.stop(config, running)

    status = common.verdict(rows)
    common.summarize(common.table("Smoke: %s" % status, rows))
    common.log_rows(rows)
    common.set_output("ready_url", config.ready_url)
    return common.finish(status, config.fail_on)


def _route(config, spec):
    path, _, expected = spec.partition("=")
    expected = int(expected) if expected.strip() else 200
    url = path if urlsplit(path).scheme else urljoin(config.ready_url, path)
    status = server.status(url)
    return (
        "route %s" % path,
        status == expected,
        "%s -> %s (expected %d)" % (url, status, expected),
    )


if __name__ == "__main__":
    common.run(main)
