"""Every endpoint URL the agent uses, loaded from endpoints.json."""

import json
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "endpoints.json")

with open(PATH, encoding="utf-8") as _handle:
    _DATA = json.load(_handle)

GITHUB_API_ROOT = _DATA["github_api_root"]
PROVIDERS = _DATA["providers"]
