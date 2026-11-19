"""ADR 0176 R2: rename the Python import packages and distributions, `choregos_*` to `tranibor_*`.

The rename is a script, not a hand edit, for two reasons: it touches about 2,500 files on a trunk
that other sessions merge into every day, and a rename done by hand misses a spelling. A branch that
falls behind `origin/main` is not rebased through hundreds of mechanical conflicts: it is rebuilt,
by resetting to `origin/main`, running this script, and replaying the hand-written commits on top.

    uv run python tools/rename_python_packages.py            # rewrite the working tree
    uv run python tools/rename_python_packages.py --check    # list what is left, exit 1 if any

What it renames, and only this:

- the directories `src/choregos_<x>` (`git mv`, so history follows);
- the module name `choregos_<x>` (a whole word: `choregos_api.rbac`, `src/choregos_core/`, but not
  `choregos_api_secret`) in every tracked text file;
- the distribution name `choregos-<x>` where it names a distribution: in a `pyproject.toml`, after
  `--package`, `--no-install-package`, `metadata.version(` and the like, and, for the names no other
  thing carries (`core`, `contracts`, `adapters`, `agent-sdk`, `cli`, `tools-mcp`), in prose too.

What it never renames (ADR 0176, point 6, and the rounds that follow): images and charts (R3,
`choregos-api` is also an image), environment variables (R4), headers, cookies, labels, queues, the
console scripts `choregos-api`/`choregos-outpost`/... (commands an operator and a stored job type),
the entry-point groups `choregos.plugins` and `choregos.migrations`, the generated client
`choregos_tools` that agents' scripts import, and history (`docs/adr`, `docs/plan`, recorded
Temporal histories, trials).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: (distribution suffix, module suffix, the directory holding `src/`)
PACKAGES: tuple[tuple[str, str, str], ...] = (
    ("contracts", "contracts", "packages/contracts"),
    ("core", "core", "packages/core"),
    ("adapters", "adapters", "packages/adapters"),
    ("runner", "runner", "packages/runner"),
    ("quant", "quant", "packages/quant"),
    ("tools-mcp", "tools_mcp", "packages/tools-mcp"),
    ("playbooks", "playbooks", "packages/playbooks"),
    ("cli", "cli", "packages/cli"),
    ("agent-sdk", "agent_sdk", "packages/agent-sdk"),
    ("outpost", "outpost", "packages/outpost"),
    ("api", "api", "apps/api"),
    ("orchestrator", "orchestrator", "apps/orchestrator"),
)

#: History says what was true; a recorded run is evidence, not source.
EXCLUDED_PREFIXES = (
    "docs/adr/",
    "docs/plan/",
    "tests/replay/histories/",
    "trials/",
    "uv.lock",
    "tools/rename_python_packages.py",
    # The module that implements the aliases says the old names in its examples.
    "packages/contracts/src/tranibor_contracts/_compat.py",
    # The compatibility shims are written with the old names, on purpose.
    "compat/",
    "tests/compat/",
)
#: Prose files a human edits by hand once the script has run (they say both names on purpose).
SKIPPED_SUFFIXES = (".jpeg", ".png", ".jpg", ".gif", ".ico", ".woff2", ".woff", ".pdf", ".zip", ".whl")

#: `\b` is not enough: in source text `"x\nchoregos_api"` has the escape `\n` right before the name.
MODULE = re.compile(r"(?:\b|(?<=\\[nrt]))choregos_(" + "|".join(m for _, m, _ in PACKAGES) + r")\b")
#: In history (ADRs, plan) only a path is rewritten: a page that names `src/choregos_core/x.py` must name a
#: file that exists (`tests/docs/test_docs_name_existing_paths.py`); what it says about the name stays.
HISTORY_PATH = re.compile(r"(?<=src/)choregos_(" + "|".join(m for _, m, _ in PACKAGES) + r")\b")
HISTORY_PREFIXES = ("docs/adr/", "docs/plan/")
#: A line that carries this marker names an old package or distribution on purpose (a shim).
KEEP = "compat-ok"

#: Names that are a distribution and nothing else, so prose may be rewritten too. `cli` is followed
#: by `/` in a User-Agent (`choregos-cli/1.2`), which is a wire token and stays.
UNAMBIGUOUS = ("core", "contracts", "adapters", "agent-sdk", "cli", "tools-mcp")
PROSE_DIST = re.compile(r"(?<![\w./-])choregos-(" + "|".join(UNAMBIGUOUS) + r")(?![\w/-]|\.[a-z])")

#: Where an ambiguous name (`choregos-api` is an image, a service, a console script) is a distribution.
CONTEXT_DIST = re.compile(
    r"(?P<ctx>(?:--package[= ]|--no-install-package[= ]|--only-package[= ]|--include-distribution-metadata=|"
    r"metadata\.version\(\s*|\bversion\(\s*)[\"']?)choregos-(?P<name>"
    + "|".join(d for d, _, _ in PACKAGES)
    + r")\b"
)

#: In a pyproject: `"choregos-core[telemetry]"`, `name = "choregos-api"`, a `workspace = true` source.
PYPROJECT_DIST = re.compile(
    r"(?<![\w.-])choregos-(" + "|".join(d for d, _, _ in PACKAGES) + r")(?=[\"\[=<>! ,]|$)"
)
SCRIPT_LINE = re.compile(r"^choregos-[\w-]+ = \"(?:choregos|tranibor)_")


def run(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True).stdout  # noqa: S603


def tracked() -> list[str]:
    out = run("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return [p for p in out.split("\0") if p]


def excluded(path: str) -> bool:
    return path.startswith(EXCLUDED_PREFIXES) or path.endswith(SKIPPED_SUFFIXES)


def rewrite_pyproject(text: str) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        if SCRIPT_LINE.match(line):
            lines.append(MODULE.sub(lambda m: f"tranibor_{m.group(1)}", line))
        else:
            lines.append(PYPROJECT_DIST.sub(lambda m: f"tranibor-{m.group(1)}", line))
    return "".join(lines)


def rewrite(path: str, text: str) -> str:
    if KEEP in text:
        return "".join(
            line if KEEP in line else rewrite(path, line) for line in text.splitlines(keepends=True)
        )
    text = MODULE.sub(lambda m: f"tranibor_{m.group(1)}", text)
    text = CONTEXT_DIST.sub(lambda m: f"{m.group('ctx')}tranibor-{m.group('name')}", text)
    if path.endswith("pyproject.toml"):
        return rewrite_pyproject(text)
    return PROSE_DIST.sub(lambda m: f"tranibor-{m.group(1)}", text)


def move_directories() -> None:
    for _, module, directory in PACKAGES:
        old = ROOT / directory / "src" / f"choregos_{module}"
        new = ROOT / directory / "src" / f"tranibor_{module}"
        if old.exists() and not new.exists():
            run("git", "mv", str(old.relative_to(ROOT)), str(new.relative_to(ROOT)))
        elif old.exists():
            # A file added under the old name by a merge: move it by hand into the new tree.
            for src in sorted(p for p in old.rglob("*") if p.is_file()):
                dest = new / src.relative_to(old)
                dest.parent.mkdir(parents=True, exist_ok=True)
                run("git", "mv", str(src.relative_to(ROOT)), str(dest.relative_to(ROOT)))


def apply() -> int:
    move_directories()
    changed = 0
    for path in tracked():
        history = path.startswith(HISTORY_PREFIXES)
        if excluded(path) and not history:
            continue
        file = ROOT / path
        if not file.is_file() or file.is_symlink():
            continue
        try:
            before = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        after = (
            HISTORY_PATH.sub(lambda m: f"tranibor_{m.group(1)}", before) if history else rewrite(path, before)
        )
        if after != before:
            file.write_text(after, encoding="utf-8")
            changed += 1
    print(f"rewrote {changed} files")
    return 0


def check() -> int:
    left: list[str] = []
    for path in tracked():
        history = path.startswith(HISTORY_PREFIXES)
        if excluded(path) and not history:
            continue
        file = ROOT / path
        if not file.is_file() or file.is_symlink():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if (
            HISTORY_PATH.sub(lambda m: f"tranibor_{m.group(1)}", text) if history else rewrite(path, text)
        ) != text:
            left.append(path)
    for path in left:
        print(path)
    return 1 if left else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="list the files the script would still change")
    args = parser.parse_args()
    return check() if args.check else apply()


if __name__ == "__main__":
    sys.exit(main())
