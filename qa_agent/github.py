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


def _paginate(token, path, api_root=None):
    """Every item of a list endpoint, following per_page/page like the API does."""
    items = []
    page = 1
    while True:
        chunk = _call(token, "GET", "%s?per_page=100&page=%d" % (path, page), api_root=api_root)
        items.extend(chunk)
        if len(chunk) < 100:
            return items
        page += 1


def list_files(token, repo, number, api_root=None):
    """The PR's changed files: filename, status, additions, deletions."""
    return _paginate(token, "/repos/%s/pulls/%d/files" % (repo, number), api_root=api_root)


def list_commits(token, repo, number, api_root=None):
    return _paginate(token, "/repos/%s/pulls/%d/commits" % (repo, number), api_root=api_root)


def add_labels(token, repo, number, labels, api_root=None):
    return _call(
        token,
        "POST",
        "/repos/%s/issues/%d/labels" % (repo, number),
        body={"labels": list(labels)},
        api_root=api_root,
    )


def dependency_graph_enabled(token, repo, base, head, api_root=None):
    """Whether the dependency graph is on, which dependency review needs."""
    try:
        _call(
            token,
            "GET",
            "/repos/%s/dependency-graph/compare/%s...%s" % (repo, base, head),
            api_root=api_root,
        )
    except GitHubError:
        return False
    return True


def post_report(token, repo, number, body, api_root=None):
    """Post the QA report as a new comment, leaving earlier reports in place."""
    return _call(
        token,
        "POST",
        "/repos/%s/issues/%d/comments" % (repo, number),
        body={"body": body},
        api_root=api_root,
    )
