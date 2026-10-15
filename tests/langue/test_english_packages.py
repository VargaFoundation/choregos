"""A package translated to English stays English, on every line (S34-04).

`tools/check_english.py` reads the lines a pull request adds: it stops new French, but a package
already translated could take French back through a line moved, restored or merged from an old
branch. This test reads EVERY line of the translated packages with the same detector. A package
joins `ENGLISH` in the pull request that translates it; `STILL_FRENCH` can only shrink — an entry
that matches nothing any more turns the suite red.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The translated packages, each with the pull request that translated it.
ENGLISH = (
    "packages/ontology/",  # #467
    "packages/cli/",  # #470
    "packages/playbooks/",
    "packages/tools-mcp/",  # #529
    "tools/",  # #530
    "charts/",  # #534
    ".github/",  # S34-04f
    "dev/",  # S34-04f
    "demo/",  # S34-04g
    "tests/cluster/",  # S34-04i
    "tests/live/",  # S34-04i
    "tests/licences/",  # S34-04i
    "tests/ci/",  # S34-04i
    "tests/dev/",  # S34-04i
    "tests/integrations/",  # S34-04i
    "tests/README.md",  # S34-04i
)

#: (file, start of the line) — French that stays, each with its reason.
STILL_FRENCH = {
    # The banner `gen_contracts` writes into `packages/contracts/ts/`: changing it rewrites the
    # generated contracts, which is a `contract-change` pull request (ADR 0001).
    ("tools/gen_contracts.py", " * Généré par tools/gen_contracts.py"),  # lang: fr-ok
    ("tools/gen_contracts.py", " * Régénérer : make contracts"),  # lang: fr-ok
}

_SKIPPED_PARTS = {".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


ce = _load()


def _scanned_files(prefix: str) -> list[str]:
    files = []
    # A prefix may name one file (`tests/README.md`): `rglob` finds nothing under a file.
    for path in sorted([ROOT / prefix] if (ROOT / prefix).is_file() else (ROOT / prefix).rglob("*")):
        relative = path.relative_to(ROOT).as_posix()
        if path.is_file() and not _SKIPPED_PARTS & set(path.parts) and ce.is_scanned(relative):
            files.append(relative)
    return files


def _french_in(path: str, content: str) -> list[object]:
    every_line = set(range(1, len(content.splitlines()) + 1))
    return list(ce.findings_in(path, content, every_line))


def _all_findings() -> list[object]:
    found = []
    for prefix in ENGLISH:
        for path in _scanned_files(prefix):
            found.extend(_french_in(path, (ROOT / path).read_text(encoding="utf-8")))
    return found


def _allowed(finding: object) -> bool:
    return any(
        finding.path == path and finding.text.startswith(start)  # type: ignore[attr-defined]
        for path, start in STILL_FRENCH
    )


@pytest.mark.parametrize("prefix", ENGLISH)
def test_every_translated_package_is_read(prefix: str) -> None:
    """A package the detector does not read would pass vacuously."""
    assert _scanned_files(prefix), f"{prefix}: no file read — moved, or excluded from the scanner"


def test_translated_packages_hold_no_french() -> None:
    french = [str(f) for f in _all_findings() if not _allowed(f)]
    assert not french, "French in a package translated to English:\n" + "\n".join(french)


def test_still_french_only_shrinks() -> None:
    found = _all_findings()
    stale = [
        entry
        for entry in STILL_FRENCH
        if not any(f.path == entry[0] and f.text.startswith(entry[1]) for f in found)  # type: ignore[attr-defined]
    ]
    assert not stale, f"no longer French, remove from STILL_FRENCH: {stale}"


def test_a_french_comment_in_a_translated_package_is_caught() -> None:
    content = '"""The sidecar."""\n\n# le serveur répond avant la fin du run\nx = 1\n'  # lang: fr-ok
    assert _french_in("packages/tools-mcp/src/choregos_tools_mcp/x.py", content)
