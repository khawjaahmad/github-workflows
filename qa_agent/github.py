"""The slice of the GitHub REST API the QA agent needs."""

import json
import urllib.error
import urllib.request

API_ROOT = "https://api.github.com"
REPORT_MARKER = "<!-- qa-changes-report -->"
PER_PAGE = 100
MAX_PAGES = 20


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


def find_report(token, repo, number, api_root=None):
    """The id of this agent's existing report comment, if it posted one."""
    for page in range(1, MAX_PAGES + 1):
        comments = _call(
            token,
            "GET",
            "/repos/%s/issues/%d/comments?per_page=%d&page=%d" % (repo, number, PER_PAGE, page),
            api_root=api_root,
        )
        if not isinstance(comments, list) or not comments:
            return None
        for comment in comments:
            if REPORT_MARKER in (comment.get("body") or ""):
                return comment["id"]
        if len(comments) < PER_PAGE:
            return None
    return None


def upsert_report(token, repo, number, body, api_root=None):
    """Post the QA report, replacing this agent's previous report if present."""
    body = "%s\n%s" % (REPORT_MARKER, body)
    existing = find_report(token, repo, number, api_root=api_root)
    if existing is not None:
        return _call(
            token,
            "PATCH",
            "/repos/%s/issues/comments/%d" % (repo, existing),
            body={"body": body},
            api_root=api_root,
        )
    return _call(
        token,
        "POST",
        "/repos/%s/issues/%d/comments" % (repo, number),
        body={"body": body},
        api_root=api_root,
    )
