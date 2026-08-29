"""Shared fixtures: stub OpenAI-compatible and GitHub servers, and a Config."""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_agent import config as config_module  # noqa: E402

PULL_REQUEST = {
    "number": 7,
    "title": "Add /api/health endpoint",
    "body": "Returns 200 with the version.",
    "base": {"ref": "main", "sha": "base1234", "repo": {"full_name": "acme/widget"}},
    "head": {"ref": "feature", "sha": "head5678"},
}


def tool_call(name, arguments, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def report_turn(status="PASS", call_id="call_report", **extra):
    arguments = {"status": status, "changes_tested": "- something", "evidence": "- output"}
    arguments.update(extra)
    return {"content": None, "tool_calls": [tool_call("submit_report", arguments, call_id)]}


def bash_turn(command, call_id="call_1"):
    return {"content": None, "tool_calls": [tool_call("bash", {"command": command}, call_id)]}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.handle_request_for(self, "GET")

    def do_POST(self):
        self.server.handle_request_for(self, "POST")

    def do_PATCH(self):
        self.server.handle_request_for(self, "PATCH")

    def log_message(self, *args):
        pass


class _Server(HTTPServer):
    def start(self):
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    @property
    def url(self):
        host, port = self.server_address
        return "http://%s:%d" % (host, port)

    def read_body(self, handler):
        length = int(handler.headers.get("Content-Length") or 0)
        return json.loads(handler.rfile.read(length)) if length else {}

    def reply_raw(self, handler, status, raw, content_type="text/plain"):
        raw = raw.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    def reply(self, handler, status, payload, headers=()):
        raw = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        for name, value in headers:
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(raw)


class LLMServer(_Server):
    """Replays scripted assistant turns and records what the agent sent.

    A turn may be a message dict, or an int status code to simulate a provider
    error on that turn.
    """

    def __init__(self, turns=()):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.turns = list(turns)
        self.requests = []

    def handle_request_for(self, handler, method):
        self.requests.append(self.read_body(handler))
        turn = self.turns.pop(0) if self.turns else 500
        if isinstance(turn, int):
            self.reply(handler, turn, {"error": {"message": "stub failure"}})
            return
        if isinstance(turn, tuple):
            status, payload = turn
            self.reply(handler, status, payload)
            return
        self.reply(handler, 200, {"choices": [{"message": turn}]})

    @property
    def last_messages(self):
        return self.requests[-1]["messages"]


class GitHubServer(_Server):
    """An in-memory issue-comments API, paginated like the real one."""

    def __init__(self, comments=(), pull_request=None, diff="diff --git a/a b/a"):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.comments = [dict(comment) for comment in comments]
        self.pull_request = pull_request or PULL_REQUEST
        self.diff = diff
        self.next_id = 1000
        self.calls = []

    def handle_request_for(self, handler, method):
        parsed = urlparse(handler.path)
        self.calls.append((method, parsed.path, parsed.query))

        if method == "GET" and "/pulls/" in parsed.path:
            if (handler.headers.get("Accept") or "").endswith("diff"):
                self.reply_raw(handler, 200, self.diff)
            else:
                self.reply(handler, 200, self.pull_request)
            return

        if method == "GET" and parsed.path.endswith("/comments"):
            query = parse_qs(parsed.query)
            per_page = int(query.get("per_page", ["30"])[0])
            page = int(query.get("page", ["1"])[0])
            start = (page - 1) * per_page
            self.reply(handler, 200, self.comments[start : start + per_page])
            return

        if method == "POST" and parsed.path.endswith("/comments"):
            self.next_id += 1
            comment = {"id": self.next_id, "body": self.read_body(handler)["body"]}
            self.comments.append(comment)
            self.reply(handler, 201, comment)
            return

        if method == "PATCH":
            comment_id = int(parsed.path.rsplit("/", 1)[-1])
            for comment in self.comments:
                if comment["id"] == comment_id:
                    comment["body"] = self.read_body(handler)["body"]
                    self.reply(handler, 200, comment)
                    return

        self.reply(handler, 404, {"message": "not found"})


def make_config(**overrides):
    settings = dict(
        api_key="test-key",
        base_url="http://127.0.0.1:1/v1",
        model="glm-5.3",
        provider="zai",
        github_token="token",
        repo="acme/widget",
        pr_number=7,
        workspace=os.getcwd(),
        max_turns=10,
        command_timeout=30,
        post_comment=False,
        setup_command="",
        effort="",
        request_timeout=10,
        time_budget=0,
        max_context_chars=0,
        artifacts_dir="",
        run_url="",
        fail_on=("FAIL",),
    )
    settings.update(overrides)
    return config_module.Config(**settings)
