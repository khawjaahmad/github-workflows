"""Endpoint URLs live in endpoints.json and nowhere else."""

import glob
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_agent import config as config_module, endpoints, github  # noqa: E402

PACKAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qa_agent")


class EndpointsTest(unittest.TestCase):
    def setUp(self):
        original = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(original)))

    def test_the_json_is_the_only_place_a_url_is_written(self):
        offenders = {}
        for path in sorted(glob.glob(os.path.join(PACKAGE, "*.py"))):
            found = re.findall(r"https?://[^\s\"')]+", open(path, encoding="utf-8").read())
            if found:
                offenders[os.path.basename(path)] = found
        self.assertEqual(offenders, {}, "move these URLs into endpoints.json")

    def test_config_and_the_github_client_read_the_same_file(self):
        data = json.load(open(endpoints.PATH, encoding="utf-8"))

        self.assertEqual(config_module.PROVIDERS, data["providers"])
        self.assertEqual(github.API_ROOT, data["github_api_root"])
        self.assertEqual(config_module.Config.github_api_url, data["github_api_root"])

    def test_every_provider_resolves_to_its_url(self):
        for name, url in endpoints.PROVIDERS.items():
            with self.subTest(provider=name):
                os.environ.update(
                    {
                        "QA_API_KEY": "k",
                        "QA_MODEL": "m",
                        "QA_REPO": "a/b",
                        "QA_PR_NUMBER": "7",
                        "QA_PROVIDER": name,
                    }
                )
                self.assertEqual(config_module.from_env().base_url, url)


if __name__ == "__main__":
    unittest.main()
