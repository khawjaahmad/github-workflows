"""Lint the SQL migrations a pull request adds or changes, and run the repo's own drift check."""

import fnmatch
import os
import shutil
from dataclasses import dataclass

from qa_agent import tools

from . import common, server

DEFAULT_GLOBS = "*migrations/*.sql,migrations/*.sql,*migrate/*.sql,db/*.sql"
LINTERS = {"postgres": "squawk"}


@dataclass(frozen=True)
class Config:
    workspace: str
    sql_globs: tuple
    dialect: str
    scope: str
    base_ref: str
    check_command: str
    command_timeout: int
    artifacts_dir: str
    fail_on: tuple


def from_env():
    scope = common.env("QA_MIGRATION_SCOPE", "changed").lower()
    if scope not in ("changed", "all"):
        raise common.ConfigError("scope must be 'changed' or 'all', got %r" % scope)
    return Config(
        workspace=common.env("QA_WORKSPACE", os.getcwd()),
        sql_globs=tuple(common.globs(common.env("QA_MIGRATION_SQL_GLOBS", DEFAULT_GLOBS))),
        dialect=common.env("QA_MIGRATION_DIALECT", "postgres").lower(),
        scope=scope,
        base_ref=common.base_ref(common.env("QA_MIGRATION_BASE_REF")),
        check_command=common.env("QA_MIGRATION_CHECK_COMMAND"),
        command_timeout=common.int_env("QA_MIGRATION_COMMAND_TIMEOUT", "600"),
        artifacts_dir=common.env("QA_ARTIFACTS_DIR"),
        fail_on=common.parse_fail_on(common.env("QA_MIGRATION_FAIL_ON", "FAIL")),
    )


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def candidate_files(config):
    """SQL files in scope: the ones the PR added or changed, or every one in the tree."""
    if config.scope == "all":
        code, output = tools.execute("git ls-files", config.workspace, 60)
    else:
        if not config.base_ref:
            return None, "no base ref to diff against; set base_ref or use scope: all"
        if not common.ensure_ref(config.workspace, config.base_ref):
            return None, "base ref %s is not available in this checkout" % config.base_ref
        code, output = tools.execute(
            "git diff --name-only --diff-filter=AMR %s...HEAD" % config.base_ref,
            config.workspace,
            60,
        )
    if code != 0:
        return None, "git failed: %s" % server.tail(output)
    files = [line.strip() for line in output.splitlines() if line.strip()]
    return sorted(name for name in files if matches(name, config.sql_globs)), ""


def main():
    config = from_env()
    files, problem = candidate_files(config)
    rows = []
    if files is None:
        rows.append(("files", False, problem))
    elif not files and not config.check_command:
        common.summarize(
            "Skipping migration lint: no SQL files matching %s %s, and no `check_command`."
            % (
                ", ".join("`%s`" % g for g in config.sql_globs),
                "changed" if config.scope == "changed" else "found",
            )
        )
        common.set_output("files", "0")
        return common.finish("SKIPPED", config.fail_on)
    else:
        rows.append(
            (
                "files",
                None,
                "%d SQL file(s) %s"
                % (len(files), "changed" if config.scope == "changed" else "in the tree"),
            )
        )
        if files:
            rows.append(_lint(config, files))
        if config.check_command:
            rows.append(server.command("check", config.check_command, config))

    status = common.verdict(rows)
    common.summarize(common.table("Migration lint: %s" % status, rows))
    common.log_rows(rows)
    common.set_output("files", str(len(files or [])))
    return common.finish(status, config.fail_on)


def _lint(config, files):
    linter = LINTERS.get(config.dialect)
    if not linter:
        return ("lint", None, "no linter for dialect %r; use `check_command`" % config.dialect)
    if not shutil.which(linter):
        return ("lint", None, "%s is not installed" % linter)
    quoted = " ".join('"%s"' % name for name in files)
    row = server.command("lint", "%s %s" % (linter, quoted), config)
    return (
        row[0],
        row[1],
        "%s over %d file(s): %s" % (linter, len(files), row[2].split(": ", 1)[-1]),
    )


if __name__ == "__main__":
    common.run(main)
