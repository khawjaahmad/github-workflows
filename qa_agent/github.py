"""The slice of the GitHub REST API the QA agent needs."""

import json
import urllib.error
import urllib.request

from .endpoints import GITHUB_API_ROOT as API_ROOT


class GitHubError(Exception):
    pass


def _call(token, method, path, accept="application/vnd.github+json", body=None, api_root=None):
    request = urllib.request.Request(
        path if path.startswith("http") else (api_root or API_ROOT) + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={
            "Accept": accept,
            "Authorization": "Bearer %s" % token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "qa-changes-agent",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:1000]
        raise GitHubError("HTTP %s for %s %s: %s" % (error.code, method, path, detail))
    except urllib.error.URLError as error:
        raise GitHubError("%s %s failed: %s" % (method, path, error))
    if accept.endswith("diff"):
        return raw
    return json.loads(raw) if raw else {}


def get_pull_request(token, repo, number, api_root=None):
    return _call(token, "GET", "/repos/%s/pulls/%d" % (repo, number), api_root=api_root)


def get_diff(token, repo, number, max_chars=120000, api_root=None):
    diff = _call(
        token,
        "GET",
        "/repos/%s/pulls/%d" % (repo, number),
        accept="application/vnd.github.v3.diff",
        api_root=api_root,
    )
    if len(diff) > max_chars:
        return diff[:max_chars] + "\n\n[diff truncated — inspect the working tree with git]"
    return diff


def post_report(token, repo, number, body, api_root=None):
    """Post the QA report as a new comment, leaving earlier reports in place."""
    return _call(
        token,
        "POST",
        "/repos/%s/issues/%d/comments" % (repo, number),
        body={"body": body},
        api_root=api_root,
    )
