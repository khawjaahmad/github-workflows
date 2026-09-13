"""Boot the software, wait until it answers, hit its routes and commands."""

import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from qa_agent import tools

from . import common

POLL_SECONDS = 2
REQUEST_TIMEOUT = 10
READY_BELOW = 500


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
    server = None
    try:
        if config.setup_command:
            rows.append(_command("setup", config.setup_command, config))
        if not rows or rows[-1][1]:
            server = _start(config, rows)
            if server is not False and config.ready_url:
                rows.append(_wait_ready(config, server))
            if _healthy(rows):
                rows += [_route(config, spec) for spec in config.routes]
                rows += [_command("command", command, config) for command in config.commands]
                if config.hurl_dir:
                    rows.append(_hurl(config))
    finally:
        _stop(config, server)

    status = common.verdict(rows)
    common.summarize(common.table("Smoke: %s" % status, rows))
    for name, ok, detail in rows:
        common.log("[%s] %s: %s" % ("ok" if ok else "FAIL", name, detail))
    common.set_output("ready_url", config.ready_url)
    return common.finish(status, config.fail_on)


def _healthy(rows):
    return all(ok is not False for _, ok, _ in rows)


def _command(kind, command, config):
    code, output = tools.execute(command, config.workspace, config.command_timeout)
    tail = (output.strip().splitlines() or ["(no output)"])[-1][:200]
    if code is None:
        return (kind, False, "`%s` timed out after %ds" % (command, config.command_timeout))
    return (kind, code == 0, "`%s` exit %d: %s" % (command, code, tail))


def _start(config, rows):
    """Start the server; returns a Popen, True for compose, or False when start failed."""
    if config.compose_file:
        command = "docker compose -f %s up --detach --wait --wait-timeout %d" % (
            config.compose_file,
            config.ready_timeout,
        )
        code, output = tools.execute(command, config.workspace, config.ready_timeout + 60)
        tail = (output.strip().splitlines() or ["(no output)"])[-1][:200]
        rows.append(("compose up", code == 0, "exit %s: %s" % (code, tail)))
        return True if code == 0 else False
    if not config.start_command:
        return None
    log_path = os.path.join(config.artifacts_dir or config.workspace, "smoke-server.log")
    handle = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        ["bash", "-c", config.start_command],
        cwd=config.workspace,
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
        env=tools.child_environment(),
        start_new_session=os.name == "posix",
    )
    process.log_handle = handle
    process.log_path = log_path
    return process


def _stop(config, server):
    if server is True:
        log_path = os.path.join(config.artifacts_dir or config.workspace, "smoke-compose.log")
        tools.execute(
            "docker compose -f %s logs --no-color > %s; "
            "docker compose -f %s down --volumes --remove-orphans"
            % (config.compose_file, log_path, config.compose_file),
            config.workspace,
            120,
        )
    elif isinstance(server, subprocess.Popen):
        if server.poll() is None:
            tools.terminate(server)
        server.log_handle.close()


def _wait_ready(config, server):
    deadline = time.monotonic() + config.ready_timeout
    last = "no response"
    while time.monotonic() < deadline:
        if isinstance(server, subprocess.Popen) and server.poll() is not None:
            return (
                "ready",
                False,
                "server exited with code %d before answering; see %s"
                % (server.returncode, os.path.basename(server.log_path)),
            )
        status = _status(config.ready_url)
        if isinstance(status, int) and status < READY_BELOW:
            return ("ready", True, "%s answered %d" % (config.ready_url, status))
        last = str(status)
        time.sleep(POLL_SECONDS)
    return (
        "ready",
        False,
        "%s did not answer within %ds (last: %s)" % (config.ready_url, config.ready_timeout, last),
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _status(url):
    """The HTTP status for a GET, or an error description."""
    try:
        with _OPENER.open(url, timeout=REQUEST_TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError, ValueError) as error:
        return "%s: %s" % (type(error).__name__, getattr(error, "reason", error))


def _route(config, spec):
    path, _, expected = spec.partition("=")
    expected = int(expected) if expected.strip() else 200
    url = path if urlsplit(path).scheme else urljoin(config.ready_url, path)
    status = _status(url)
    return (
        "route %s" % path,
        status == expected,
        "%s -> %s (expected %d)" % (url, status, expected),
    )


def _hurl(config):
    report = os.path.join(config.artifacts_dir or config.workspace, "hurl-junit.xml")
    command = "hurl --test --variable base_url=%s --report-junit %s %s" % (
        config.ready_url.rstrip("/"),
        report,
        config.hurl_dir,
    )
    return _command("hurl", command, config)


if __name__ == "__main__":
    common.run(main)
