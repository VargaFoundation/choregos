"""No test touches the runner's fixed git-directory root (#2011).

The production default is `/var/tmp/choregos-git` (ADR 0146 section 5); on a shared machine another account
may own it. The root `conftest.py` points `TRANIBOR_RUNNER_GIT_DIR_ROOT` into a temporary directory for every
test; this file fails when that stops being true, or when a test names the fixed path or unsets the variable.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from tranibor_runner.workspace import DEFAULT_GIT_DIR_ROOT, GIT_DIR_ROOT_VARIABLE, git_dir_for

REPO = Path(__file__).resolve().parents[1]
THIS = Path(__file__).resolve()
UNSET = re.compile(
    r"delenv\(\s*[\"']TRANIBOR_RUNNER_GIT_DIR_ROOT|environ\.pop\(\s*[\"']TRANIBOR_RUNNER_GIT_DIR_ROOT"
)


def test_a_test_resolves_its_git_dir_under_its_own_temporary_directory(_runner_git_dirs: Path) -> None:
    root = Path(os.environ[GIT_DIR_ROOT_VARIABLE])
    assert root == _runner_git_dirs
    assert not str(root).startswith(str(DEFAULT_GIT_DIR_ROOT))
    for workspace in (Path("/work/a"), Path("/work/b")):
        assert git_dir_for(workspace).parent == root


def test_no_test_names_the_fixed_root_or_unsets_the_variable() -> None:
    offenders = []
    for path in [
        *REPO.glob("apps/*/tests/**/*.py"),
        *REPO.glob("packages/*/tests/**/*.py"),
        *REPO.glob("tests/**/*.py"),
    ]:
        if path.resolve() == THIS or "/live/" in str(path):
            continue
        for number, text in enumerate(path.read_text().splitlines(), 1):
            if "/var/tmp/choregos-git" in text or UNSET.search(text):
                offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert not offenders, f"a test would touch the fixed git-dir root: {offenders}"
