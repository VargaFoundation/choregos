"""The tree stays within its French baselines (ADR 0098, replacing the `ENGLISH` and `STILL_FRENCH`
lists of S34-04).

`tools/check_english.py` reads the lines a pull request adds; this test reads EVERY file with the
same tool and compares each file's count of French prose lines, French declared names and French
path components with `tools/english_baseline/<area>.tsv`. A count can only go down: a file above
its baseline, or a new file with French, turns the suite red — a line moved, restored or merged
from an old branch cannot take French back into a package that was translated. Lowering a baseline
is `uv run python tools/check_english.py --update-baseline --area <area>`; it refuses a rise.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


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


@pytest.mark.skipif(not _in_a_git_checkout(), reason="not a git checkout: the tree scan reads `git ls-files`")
def test_the_tree_is_within_its_baselines() -> None:
    findings = ce.scan_tree()
    baselines = ce.load_baselines()
    assert set(baselines) == set(ce.AREAS), "one baseline file per area: " + ", ".join(
        sorted(set(ce.AREAS) ^ set(baselines))
    )
    by_path: dict[str, list[object]] = {}
    for finding in findings:
        by_path.setdefault(finding.path, []).append(finding)
    risen = []
    for area, counts in ce.counts_by_area(ce.counts_per_file(findings)).items():
        rises, _ = ce.compare(counts, baselines[area])
        for change in rises:
            shown = "\n    ".join(str(f) for f in by_path[change.path][:5])
            risen.append(f"{area}: {change.path}: {change.found} > {change.baseline}\n    {shown}")
    assert not risen, "French above its baseline (ADR 0098):\n" + "\n".join(risen)


def test_a_french_comment_in_a_translated_package_is_caught() -> None:
    content = '"""The sidecar."""\n\n# le serveur répond avant la fin du run\nx = 1\n'  # lang: fr-ok
    found = ce.file_findings("packages/tools-mcp/src/choregos_tools_mcp/x.py", content)
    assert [(f.kind, f.line) for f in found] == [("prose", 3)]
