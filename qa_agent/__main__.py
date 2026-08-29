"""Entry point: gather PR context, run the agent, post the report."""

import os
import sys

from . import agent, config as config_module, github, llm


def main():
    try:
        config = config_module.from_env()
    except config_module.ConfigError as error:
        print("configuration error: %s" % error, file=sys.stderr)
        return 2

    agent.log(
        "qa-changes: provider=%s model=%s pr=%s#%d"
        % (config.provider, config.model, config.repo, config.pr_number)
    )

    try:
        pull_request = github.get_pull_request(config.github_token, config.repo, config.pr_number)
        diff = github.get_diff(config.github_token, config.repo, config.pr_number)
    except github.GitHubError as error:
        print("could not read the pull request: %s" % error, file=sys.stderr)
        return 2

    try:
        status, body = agent.run(config, pull_request, diff)
    except llm.LLMError as error:
        print("the model endpoint failed: %s" % error, file=sys.stderr)
        return 2

    agent.log("\n%s" % body)

    if config.post_comment:
        try:
            github.upsert_report(config.github_token, config.repo, config.pr_number, body)
            agent.log("posted the QA report to %s#%d" % (config.repo, config.pr_number))
        except github.GitHubError as error:
            print("could not post the QA report: %s" % error, file=sys.stderr)
            return 2

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(body + "\n")

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write("status=%s\n" % status)

    return 1 if status == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
