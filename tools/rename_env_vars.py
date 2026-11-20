#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""ADR 0176 R4: rename the environment variables, `CHOREGOS_*` to `TRANIBOR_*`.

    uv run python tools/rename_env_vars.py            # rewrite the working tree
    uv run python tools/rename_env_vars.py --check    # list what is left, exit 1 if any

A script, not a hand edit, because it touches about 800 files on a trunk other sessions merge into every
day: a branch that falls behind `origin/main` is not rebased through hundreds of conflicts, it is rebuilt
(reset to `origin/main`, run this, replay the hand-written commits), as for `rename_python_packages.py`.

What it renames: every `CHOREGOS_<NAME>` (and the prefix alone, in `env_prefix="CHOREGOS_"` and
`f"CHOREGOS_{name}"`) in a tracked text file. The process reads the new name; the old one is adopted by
`tranibor_contracts.env` when a package is imported, so a deployment that sets the old name keeps working.

What it never renames, each for a reason:

- history (`docs/adr`, `docs/plan`, recorded Temporal histories, `trials/`) says what was true;
- `charts/` and `tests/charts/`: the charts keep rendering the former names until the deployments have
  moved their values (a chart that rendered both `TRANIBOR_X` and the old `CHOREGOS_X` set by a values
  file's `extraEnv` would make the chart's default win over the deployment's choice); `docker/`: an
  image's `ENV` is a default, and a default under the new name would beat the old name a chart or a
  derived image (the enterprise edition) sets;
- the install scripts, which a user runs without this repository, the console (`apps/web`, `apps/vscode`:
  Node reads and prose are by hand, and no Node locally), `packages/contracts/openapi.yaml` and its
  generated types (a contract change carries them), and `tranibor_contracts/env.py` itself;
- a line carrying `compat-ok`, which names the old variable on purpose.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_PREFIXES = (
    "docs/adr/",
    "docs/plan/",
    "tests/replay/histories/",
    "trials/",
    "uv.lock",
    "charts/",
    "tests/charts/",
    "docker/",
    "apps/web/",
    "apps/vscode/",
    "apps/api/src/tranibor_api/cli_install/",
    "packages/contracts/openapi.yaml",
    "packages/contracts/ts/",
    "packages/contracts/src/tranibor_contracts/env.py",
    "tests/compat/",
    # a golden render of what `process` hands a job: both spellings, on purpose (R4 adoption)
    "packages/adapters/tests/fixtures/separate_agent/",
    "tools/rename_env_vars.py",
    "compat/",
)
SKIPPED_SUFFIXES = (
    ".jpeg",
    ".png",
    ".jpg",
    ".gif",
    ".ico",
    ".woff2",
    ".woff",
    ".pdf",
    ".zip",
    ".whl",
    ".lock",
)
KEEP = "compat-ok"

#: `CHOREGOS_` not preceded by a word character (so `__CHOREGOS_INSTANCE_URL__`, a template token of the
#: install scripts, and `X_CHOREGOS_Y` stay).
VARIABLE = re.compile(r"(?<![A-Za-z0-9_])CHOREGOS_")


def run(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True).stdout  # noqa: S603


def tracked() -> list[str]:
    out = run("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return [p for p in out.split("\0") if p]


def excluded(path: str) -> bool:
    return path.startswith(EXCLUDED_PREFIXES) or path.endswith(SKIPPED_SUFFIXES)


def rewrite(text: str) -> str:
    if KEEP in text:
        return "".join(
            line if KEEP in line else VARIABLE.sub("TRANIBOR_", line)
            for line in text.splitlines(keepends=True)
        )
    return VARIABLE.sub("TRANIBOR_", text)


def candidates() -> list[tuple[Path, str]]:
    found = []
    for path in tracked():
        if excluded(path):
            continue
        file = ROOT / path
        if not file.is_file() or file.is_symlink():
            continue
        try:
            found.append((file, file.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue
    return found


def apply() -> int:
    changed = 0
    for file, before in candidates():
        after = rewrite(before)
        if after != before:
            file.write_text(after, encoding="utf-8")
            changed += 1
    print(f"rewrote {changed} files")
    return 0


def check() -> int:
    left = [str(file.relative_to(ROOT)) for file, text in candidates() if rewrite(text) != text]
    for path in left:
        print(path)
    return 1 if left else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="list the files the script would still change")
    return check() if parser.parse_args().check else apply()


if __name__ == "__main__":
    sys.exit(main())
