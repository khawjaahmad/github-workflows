"""Entry point: gather PR context, run the agent, post the report."""

import os
import sys

from . import agent, config as config_module, github


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

    if config.artifacts_dir:
        os.makedirs(config.artifacts_dir, exist_ok=True)

    try:
        pull_request = github.get_pull_request(
            config.github_token, config.repo, config.pr_number, api_root=config.github_api_url
        )
        diff = github.get_diff(
            config.github_token,
            config.repo,
            config.pr_number,
            max_chars=_diff_budget(config),
            api_root=config.github_api_url,
        )
    except github.GitHubError as error:
        print("could not read the pull request: %s" % error, file=sys.stderr)
        return 2

    status, body = agent.run(config, pull_request, diff)
    agent.log("\n%s" % body)
    _publish(config, status, body)
    return 1 if status in config.fail_on else 0


DIFF_CHARS = 120000


def _diff_budget(config):
    """Keep the diff from crowding out the run that has to follow it."""
    if config.context_tokens <= 0:
        return DIFF_CHARS
    window = config.context_tokens * agent.CHARS_PER_TOKEN
    return max(20000, min(DIFF_CHARS, window // 8))


def _publish(config, status, body):
    """Get the report in front of the reviewer, by every route available."""
    if config.post_comment:
        try:
            github.upsert_report(
                config.github_token,
                config.repo,
                config.pr_number,
                body,
                api_root=config.github_api_url,
            )
            agent.log("posted the QA report to %s#%d" % (config.repo, config.pr_number))
        except github.GitHubError as error:
            print(
                "::error title=QA report could not be posted::%s "
                "(does the job grant `pull-requests: write`?)" % error,
                file=sys.stderr,
            )

    _append(os.environ.get("GITHUB_STEP_SUMMARY"), body + "\n")
    _append(os.environ.get("GITHUB_OUTPUT"), "status=%s\n" % status)


def _append(path, text):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


if __name__ == "__main__":
    sys.exit(main())
