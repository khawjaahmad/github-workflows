"""The slice of the GitHub REST API the QA agent needs."""

import json
import urllib.error
import urllib.request

API_ROOT = "https://api.github.com"
# Lets a re-run replace its previous report instead of stacking comments.
REPORT_MARKER = "<!-- qa-changes-report -->"


class GitHubError(Exception):
    pass


def _call(token, method, path, accept="application/vnd.github+json", body=None):
    request = urllib.request.Request(
        path if path.startswith("http") else API_ROOT + path,
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


def get_pull_request(token, repo, number):
    return _call(token, "GET", "/repos/%s/pulls/%d" % (repo, number))


def get_diff(token, repo, number, max_chars=120000):
    diff = _call(
        token,
        "GET",
        "/repos/%s/pulls/%d" % (repo, number),
        accept="application/vnd.github.v3.diff",
    )
    if len(diff) > max_chars:
        return diff[:max_chars] + "\n\n[diff truncated — inspect the working tree with git]"
    return diff


def upsert_report(token, repo, number, body):
    """Post the QA report, replacing this agent's previous report if present."""
    body = "%s\n%s" % (REPORT_MARKER, body)
    comments = _call(token, "GET", "/repos/%s/issues/%d/comments?per_page=100" % (repo, number))
    for comment in comments if isinstance(comments, list) else []:
        if REPORT_MARKER in (comment.get("body") or ""):
            return _call(
                token,
                "PATCH",
                "/repos/%s/issues/comments/%d" % (repo, comment["id"]),
                body={"body": body},
            )
    return _call(
        token,
        "POST",
        "/repos/%s/issues/%d/comments" % (repo, number),
        body={"body": body},
    )
