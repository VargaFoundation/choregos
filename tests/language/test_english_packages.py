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


@pytest.mark.skipif(not _in_a_git_checkout(), reason="not a git checkout: the search reads `git grep`")
def test_no_internal_alias_remains_under_packages() -> None:
    """An internal alias or shim (`alias:internal`) lives until its last user switched (ADR 0098 §3);
    S45-40 deleted the ones defined under `packages/`, the user of a package alias having switched."""
    found = subprocess.run(
        ["git", "grep", "-n", "alias:internal", "--", "packages"],
        check=False,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert found.returncode == 1, found.stdout  # `git grep` exits 1 when nothing matches
    assert found.stdout == ""


@pytest.mark.skipif(not _in_a_git_checkout(), reason="not a git checkout: the search reads `git grep`")
def test_no_internal_alias_remains_under_apps() -> None:
    """S45-46 deleted every internal alias and shim defined under `apps/api` and `apps/orchestrator`,
    each user having switched; S45-47 dropped the test suite's former database variable."""
    found = subprocess.run(
        ["git", "grep", "-n", "alias:internal", "--", "apps"],
        check=False,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert found.returncode == 1, found.stdout
    assert found.stdout == ""


@pytest.mark.skipif(not _in_a_git_checkout(), reason="not a git checkout: the search reads `git grep`")
@pytest.mark.parametrize("marker", ["alias:internal", "alias:external"])
def test_no_alias_remains_anywhere(marker: str) -> None:
    """Both tracks are closed: no `alias:internal` is left, and the external aliases' window closed
    with 0.23.0 (S45-49, ADR 0098 'Windows'), outside the pages that explain the escape."""
    found = subprocess.run(
        [
            "git",
            "grep",
            "-n",
            marker,
            "--",
            ".",
            ":(exclude)docs",
            ":(exclude)AGENTS.md",
            ":(exclude)tests/language",
            ":(exclude)tests/web",
            ":(exclude)tools",
            ":(exclude)packages/core/src/choregos_core/compat.py",
        ],
        check=False,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert found.returncode == 1, found.stdout
    assert found.stdout == ""


def test_every_baseline_is_empty() -> None:
    """The ratchet is closed (S45-47): every `tools/english_baseline/*.tsv` holds its header and no
    data line. The MCP-gateway (S46) stream finished, so there is no leftover to list."""
    full = []
    for path in sorted((ROOT / "tools" / "english_baseline").glob("*.tsv")):
        data = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if data:
            full.append(f"{path.name}: {len(data)} line(s), first {data[0]!r}")
    assert not full, "a baseline is not empty (ADR 0098, the ratchet is at zero):\n" + "\n".join(full)
