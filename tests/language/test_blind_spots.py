"""The blind spots of the English ratchet stay closed (S45-45).

The lexicon judges a name by the French words it holds; two things escape it, and this file pins
both: a path whose only French words are weak (`test_x_de_y` holds one function word, so the path
rule, which needs two, does not see it), and the areas no package story owns.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import subprocess
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WEAK_WORD_PATH = re.compile(r"(^|[/_.-])(de|du|des|d|l|la|le|les|au|aux|sans|par|pour|sur|et|un|une)([/_.-])")
UNOWNED_AREAS = ("packages-other", "repo", "web")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


ce = _load()


def _in_a_git_checkout() -> bool:
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, check=True, capture_output=True
        )
    except (subprocess.CalledProcessError, OSError):
        return False
    return True


def _tracked() -> list[str]:
    listing = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True)
    return [name for name in listing.stdout.split("\0") if name]


@pytest.mark.skipif(not _in_a_git_checkout(), reason="not a git checkout: reads `git ls-files`")
def test_no_single_weak_word_french_path() -> None:
    """Every tracked path with a weak French word in it is a decision: renamed, or frozen with a reason."""
    undecided = [path for path in _tracked() if WEAK_WORD_PATH.search(path) and path not in ce.FROZEN_PATHS]
    assert undecided == [], f"rename these, or add them to FROZEN_PATHS with a reason: {undecided}"


def test_every_frozen_path_has_a_reason() -> None:
    assert all(reason.strip() for reason in ce.FROZEN_PATHS.values())


@pytest.mark.skipif(not _in_a_git_checkout(), reason="not a git checkout: the tree scan reads `git ls-files`")
def test_unowned_areas_are_english() -> None:
    """Measured, not read from the baseline files: the areas hold no French finding at all."""
    findings = ce.scan_tree()
    french = [f"{f.path}:{f.line}" for f in findings if ce.area_of(f.path) in UNOWNED_AREAS]
    assert french == []
