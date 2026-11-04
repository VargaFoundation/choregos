"""Does the core stay independent of the enterprise edition?

[ADR 0024](../../docs/adr/0024-two-editions.md) sets two editions: this repository is the
community core, Apache-2.0, and the enterprise edition lives elsewhere, proprietary. The boundary
holds only if it is verifiable: an `import choregos_ee` slipped in here would make this repository
unbuildable without a package nobody has the right to obtain, and — worse — would mix proprietary
code into an Apache-2.0 tree.

The direction of the dependency is the only one that works: **the enterprise knows the core, the
core does not know the enterprise**. That is what lets the enterprise edition plug in through the
`choregos.plugins` entry points without the core having to know it exists.

Three properties, and the third is the one that would be forgotten: the core must not mention the
enterprise edition in a string that a dynamic `import` would read either.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
#: The core's code trees — not the tests, which may name the edition to exercise it.
SOURCES = ("packages", "apps")
#: The module prefixes that do not belong to the core.
INTERDITS = ("choregos_ee", "choregos_enterprise")


def _files() -> list[pathlib.Path]:
    trouves: list[pathlib.Path] = []
    for tree in SOURCES:
        for path in (ROOT / tree).rglob("*.py"):
            parts = set(path.parts)
            if "tests" in parts or "__pycache__" in parts or ".venv" in parts:
                continue
            trouves.append(path)
    return sorted(trouves)


def test_il_y_a_bien_du_code_a_verifier() -> None:
    """Without this guard, moving the packages would make the suite green and empty."""
    files = _files()
    assert len(files) > 100, f"only {len(files)} files found: the paths have moved"


def test_the_core_never_imports_the_enterprise_edition() -> None:
    """Read through the AST, not a regular expression: a comment must not turn it red."""
    offenders: list[str] = []
    for path in _files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == interdit or name.startswith(f"{interdit}.") for interdit in INTERDITS):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} → {name}")
    assert not offenders, (
        "the core imports the enterprise edition:\n  "
        + "\n  ".join(offenders)
        + "\nThe direction of the dependency is the reverse: the enterprise plugs in through the "
        "`choregos.plugins` entry points (ADR 0024)."
    )


def test_the_core_does_not_load_the_edition_by_its_name() -> None:
    """An `import_module("choregos_ee")` would escape the previous test, which reads the `import`s.

    This property is the easiest to bypass by accident: the module name is written in a string to
    "make the dependency optional", and the boundary becomes decorative.
    """
    offenders: list[str] = []
    for path in _files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            sans_commentaire = line.split("#", 1)[0]
            for interdit in INTERDITS:
                if f'"{interdit}' in sans_commentaire or f"'{interdit}" in sans_commentaire:
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, (
        "the core names the enterprise edition in a string — a disguised dynamic import:\n  "
        + "\n  ".join(offenders)
    )
