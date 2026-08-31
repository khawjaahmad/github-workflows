"""Describes the repository and the toolchain available to the agent."""

import os
import platform
import shutil

MARKERS = (
    ("package.json", "Node.js"),
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "Yarn"),
    ("bun.lockb", "Bun"),
    ("deno.json", "Deno"),
    ("pyproject.toml", "Python"),
    ("requirements.txt", "Python"),
    ("Pipfile", "Pipenv"),
    ("go.mod", "Go"),
    ("Cargo.toml", "Rust"),
    ("Gemfile", "Ruby"),
    ("composer.json", "PHP"),
    ("pom.xml", "Maven"),
    ("build.gradle", "Gradle"),
    ("build.gradle.kts", "Gradle"),
    ("build.sbt", "sbt"),
    ("mix.exs", "Elixir"),
    ("pubspec.yaml", "Dart/Flutter"),
    ("CMakeLists.txt", "CMake"),
    ("Makefile", "make"),
    ("Justfile", "just"),
    ("justfile", "just"),
    ("Dockerfile", "Docker"),
    ("docker-compose.yml", "Docker Compose"),
    ("docker-compose.yaml", "Docker Compose"),
    ("compose.yaml", "Docker Compose"),
    ("Procfile", "Procfile"),
)

COMMANDS = (
    "node",
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "npx",
    "deno",
    "python3",
    "pip3",
    "uv",
    "poetry",
    "pipenv",
    "go",
    "cargo",
    "rustc",
    "java",
    "mvn",
    "gradle",
    "ruby",
    "bundle",
    "php",
    "composer",
    "dotnet",
    "swift",
    "make",
    "just",
    "cmake",
    "docker",
    "docker-compose",
    "curl",
    "wget",
    "jq",
    "git",
    "psql",
    "mysql",
    "sqlite3",
    "redis-cli",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "firefox",
    "chromedriver",
)

GUIDE_FILES = (
    ".agents/skills/qa-guide.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
)


def probe(workspace):
    """Return a Markdown fingerprint of the checkout and the runner."""
    lines = [
        "Runner: %s %s, Python %s"
        % (platform.system(), platform.machine(), platform.python_version()),
    ]

    markers = []
    for filename, label in MARKERS:
        if os.path.exists(os.path.join(workspace, filename)):
            markers.append("%s (%s)" % (filename, label))
    lines.append("Build files at the repository root: %s" % (", ".join(markers) or "none found"))

    entries = sorted(
        entry for entry in _listdir(workspace) if not entry.startswith(".") or entry == ".agents"
    )
    lines.append("Top-level entries: %s" % (", ".join(entries[:60]) or "(empty)"))

    available = [command for command in COMMANDS if shutil.which(command)]
    lines.append("Commands on PATH: %s" % (", ".join(available) or "none of the usual ones"))

    guides = [name for name in GUIDE_FILES if os.path.isfile(os.path.join(workspace, name))]
    lines.append("Documentation present: %s" % (", ".join(guides) or "none"))

    return "\n".join(lines)


def read_guide(workspace, max_chars=20000):
    """Return the repository's QA guide, if it has one."""
    path = os.path.join(workspace, ".agents", "skills", "qa-guide.md")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            content = handle.read(max_chars + 1)
    except OSError:
        return ""
    if len(content) > max_chars:
        return content[:max_chars] + "\n[qa-guide.md truncated]"
    return content


def _listdir(workspace):
    try:
        return os.listdir(workspace)
    except OSError:
        return []
