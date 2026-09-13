"""Start the software under test, wait until it answers, stop it and keep its logs.

Shared by smoke, api-contract and web-check. `config` is any object with workspace,
start_command, compose_file, ready_url, ready_timeout and artifacts_dir attributes.
"""

import os
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from qa_agent import tools

POLL_SECONDS = 2
REQUEST_TIMEOUT = 10
READY_BELOW = 500


def tail(output):
    return (output.strip().splitlines() or ["(no output)"])[-1][:200]


def start(config, rows):
    """Start the server; returns a Popen, True for compose, None when nothing to start,
    or False when starting failed (a row explains why)."""
    if config.compose_file:
        command = "docker compose -f %s up --detach --wait --wait-timeout %d" % (
            config.compose_file,
            config.ready_timeout,
        )
        code, output = tools.execute(command, config.workspace, config.ready_timeout + 60)
        rows.append(("compose up", code == 0, "exit %s: %s" % (code, tail(output))))
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


def stop(config, server):
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


def wait_ready(config, server):
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
        result = status(config.ready_url)
        if isinstance(result, int) and result < READY_BELOW:
            return ("ready", True, "%s answered %d" % (config.ready_url, result))
        last = str(result)
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


def status(url):
    """The HTTP status for a GET without following redirects, or an error description."""
    try:
        with _OPENER.open(url, timeout=REQUEST_TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError, ValueError) as error:
        return "%s: %s" % (type(error).__name__, getattr(error, "reason", error))


def command(kind, command_line, config):
    """Run one command to completion as a results row."""
    code, output = tools.execute(command_line, config.workspace, config.command_timeout)
    if code is None:
        return (kind, False, "`%s` timed out after %ds" % (command_line, config.command_timeout))
    return (kind, code == 0, "`%s` exit %d: %s" % (command_line, code, tail(output)))


def origin(url):
    """scheme://host[:port] of a URL, so a ready URL with a path can still name the server."""
    parts = urlsplit(url)
    return "%s://%s" % (parts.scheme, parts.netloc) if parts.scheme and parts.netloc else url


def hurl(config, base_url=""):
    """Run the .hurl files with `base_url` set to the server's origin (or the given base)."""
    report = os.path.join(config.artifacts_dir or config.workspace, "hurl-junit.xml")
    command_line = "hurl --test --variable base_url=%s --report-junit %s %s" % (
        (base_url or origin(config.ready_url)).rstrip("/"),
        report,
        config.hurl_dir,
    )
    return command("hurl", command_line, config)
