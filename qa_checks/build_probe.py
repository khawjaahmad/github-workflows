"""Work out how a checkout builds, from the files it actually has."""

import glob
import json
import os
import re

from . import common


def probe(workspace, override=""):
    """Returns {"detected", "build_command", "toolchain"}; detected is "" when unknown."""
    if override.strip():
        return _found("custom", override.strip(), "")
    for rule in RULES:
        found = rule(workspace)
        if found:
            return found
    return {"detected": "", "build_command": "", "toolchain": ""}


def _found(detected, command, toolchain):
    return {"detected": detected, "build_command": command, "toolchain": toolchain}


def _exists(workspace, *names):
    return any(os.path.exists(os.path.join(workspace, name)) for name in names)


def _read(workspace, name):
    try:
        with open(os.path.join(workspace, name), encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def _makefile(workspace):
    if re.search(r"^build\s*:", _read(workspace, "Makefile"), re.MULTILINE):
        return _found("make", "make build", "")


def _node(workspace):
    if not _exists(workspace, "package.json"):
        return None
    try:
        scripts = json.loads(_read(workspace, "package.json")).get("scripts") or {}
    except (ValueError, AttributeError):
        scripts = {}
    if _exists(workspace, "pnpm-lock.yaml"):
        install, run = "corepack enable && pnpm install --frozen-lockfile", "pnpm run build"
    elif _exists(workspace, "yarn.lock"):
        install, run = "corepack enable && yarn install", "yarn run build"
    elif _exists(workspace, "bun.lockb", "bun.lock"):
        install, run = "bun install --frozen-lockfile", "bun run build"
    elif _exists(workspace, "package-lock.json"):
        install, run = "npm ci", "npm run build"
    else:
        install, run = "npm install", "npm run build"
    command = "%s && %s" % (install, run) if "build" in scripts else install
    return _found("node", command, "node")


def _python(workspace):
    pyproject = _read(workspace, "pyproject.toml")
    if re.search(r"^\[(project|build-system)\]", pyproject, re.MULTILINE):
        return _found("python", "python -m pip install .", "python")
    if _exists(workspace, "requirements.txt"):
        return _found("python", "python -m pip install -r requirements.txt", "python")


def _go(workspace):
    if _exists(workspace, "go.mod"):
        return _found("go", "go build ./...", "go")


def _rust(workspace):
    if _exists(workspace, "Cargo.toml"):
        return _found("rust", "cargo build", "rust")


def _maven(workspace):
    if _exists(workspace, "pom.xml"):
        mvn = "./mvnw" if _exists(workspace, "mvnw") else "mvn"
        return _found("maven", "%s -B -DskipTests package" % mvn, "java")


def _gradle(workspace):
    if _exists(
        workspace, "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"
    ):
        gradle = "./gradlew" if _exists(workspace, "gradlew") else "gradle"
        return _found("gradle", "%s assemble" % gradle, "java")


def _dotnet(workspace):
    if glob.glob(os.path.join(workspace, "*.sln")) or glob.glob(
        os.path.join(workspace, "*.csproj")
    ):
        return _found("dotnet", "dotnet build", "dotnet")


def _ruby(workspace):
    if _exists(workspace, "Gemfile"):
        return _found("ruby", "bundle install", "ruby")


def _elixir(workspace):
    if _exists(workspace, "mix.exs"):
        return _found("elixir", "mix deps.get && mix compile", "elixir")


def _bazel(workspace):
    if _exists(workspace, "MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel"):
        return _found("bazel", "bazel build //...", "bazel")


def _docker(workspace):
    if _exists(workspace, "Dockerfile"):
        return _found("docker", "docker build .", "docker")


RULES = (
    _makefile,
    _node,
    _python,
    _go,
    _rust,
    _maven,
    _gradle,
    _dotnet,
    _ruby,
    _elixir,
    _bazel,
    _docker,
)


def main():
    workspace = common.env("QA_WORKSPACE", os.getcwd())
    directory = os.path.join(workspace, common.env("QA_BUILD_WORKING_DIRECTORY", "."))
    result = probe(directory, common.env("QA_BUILD_COMMAND"))
    for name, value in result.items():
        common.set_output(name, value)
    if result["detected"]:
        common.log("build: %s via `%s`" % (result["detected"], result["build_command"]))
    else:
        common.summarize(
            "Skipping build: no build system recognised in `%s`. Set `build_command` to "
            "tell the workflow how this repository builds." % directory
        )
        common.log("build: nothing detected")
    return 0


if __name__ == "__main__":
    common.run(main)
