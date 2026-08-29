"""The GitHub client: finding, posting and updating the report comment."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import GitHubServer  # noqa: E402

from qa_agent import github  # noqa: E402


class ReportCommentTest(unittest.TestCase):
    def serve(self, comments=()):
        server = GitHubServer(comments).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        patched = github.API_ROOT
        github.API_ROOT = server.url
        self.addCleanup(setattr, github, "API_ROOT", patched)
        return server

    def test_posts_when_there_is_no_previous_report(self):
        server = self.serve([{"id": 1, "body": "looks good to me"}])

        github.upsert_report("token", "acme/widget", 7, "## QA Report")

        self.assertEqual(len(server.comments), 2)
        self.assertIn(github.REPORT_MARKER, server.comments[-1]["body"])
        self.assertIn("POST", [method for method, _, _ in server.calls])

    def test_updates_its_own_previous_report(self):
        server = self.serve([{"id": 42, "body": github.REPORT_MARKER + "\nold report"}])

        github.upsert_report("token", "acme/widget", 7, "## QA Report\nnew")

        self.assertEqual(len(server.comments), 1)
        self.assertIn("new", server.comments[0]["body"])
        self.assertIn("PATCH", [method for method, _, _ in server.calls])

    def test_finds_a_report_beyond_the_first_page(self):
        # A busy pull request: missing the marker would stack a second report.
        comments = [{"id": index, "body": "chatter"} for index in range(250)]
        comments[240] = {"id": 240, "body": github.REPORT_MARKER + "\nold report"}
        server = self.serve(comments)

        github.upsert_report("token", "acme/widget", 7, "## QA Report\nnew")

        self.assertEqual(len(server.comments), 250)
        self.assertIn("new", server.comments[240]["body"])
        pages = [query for method, _, query in server.calls if method == "GET"]
        self.assertEqual(len(pages), 3)

    def test_stops_paging_at_the_end(self):
        server = self.serve([{"id": 1, "body": "chatter"}])
        self.assertIsNone(github.find_report("token", "acme/widget", 7))
        self.assertEqual(len([c for c in server.calls if c[0] == "GET"]), 1)

    def test_errors_carry_the_status_and_body(self):
        self.serve()
        with self.assertRaises(github.GitHubError) as raised:
            github._call("token", "PATCH", "/repos/acme/widget/issues/comments/999", body={"b": 1})
        self.assertIn("404", str(raised.exception))


class DiffTest(unittest.TestCase):
    def test_truncation_tells_the_agent_where_to_look(self):
        with mock.patch.object(github, "_call", return_value="d" * 500):
            diff = github.get_diff("token", "acme/widget", 7, max_chars=100)

        self.assertTrue(diff.startswith("d" * 100))
        self.assertIn("diff truncated", diff)
        self.assertIn("inspect the working tree with git", diff)

    def test_a_short_diff_is_passed_through(self):
        with mock.patch.object(github, "_call", return_value="diff --git a/a b/a"):
            self.assertEqual(
                github.get_diff("token", "acme/widget", 7), "diff --git a/a b/a"
            )


if __name__ == "__main__":
    unittest.main()
