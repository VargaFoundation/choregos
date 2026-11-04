# SPDX-License-Identifier: Apache-2.0
"""Every file this repository DISTRIBUTES declares its license, in the file itself.

`LICENSE` and `NOTICE` state the repository's license. They do not follow a file that is copied
elsewhere — and that is precisely what happens in an open-core model with two entities
([ADR 0024](../../docs/adr/0024-two-editions.md)): code flows between an Apache-2.0 repository
and a proprietary one, in both directions, and nothing in the file said where it came from.

`SPDX-License-Identifier` says it. It is readable by a human in one line and by a tool (`reuse`,
enterprise scanners, SBOM generators) with no configuration. A file that arrives here **without**
the header — pasted from elsewhere, or copied from the enterprise edition — turns the suite red
instead of blending into the tree.

SCOPE: what is distributed. The sources of the packages and applications, the migrations, the
front end, and the **generated** TypeScript contracts (their banner comes from the generator,
`gen_contracts.py` — writing it by hand would have turned `contracts-check` red on the first
run). Tests are not distributed; this file carries the header anyway, for consistency and because
it costs one line.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
MARQUE = "SPDX-License-Identifier: Apache-2.0"


def _shipped_files() -> list[pathlib.Path]:
    trouves: list[pathlib.Path] = []
    racines = [
        *sorted((ROOT / "packages").glob("*/src")),
        *sorted((ROOT / "apps").glob("*/src")),
        ROOT / "apps/api/src/choregos_api/migrations",
    ]
    for root in racines:
        if not root.exists():
            continue
        trouves += [f for f in sorted(root.rglob("*.py")) if "__pycache__" not in f.parts]
    for dossier in (ROOT / "apps/web/src", ROOT / "packages/contracts/ts"):
        if dossier.exists():
            trouves += [f for f in sorted(dossier.rglob("*")) if f.suffix in {".ts", ".tsx"}]
    return trouves


def test_there_are_files_to_check() -> None:
    """Without this guard, a path that changes would make the test green while looking at nothing."""
    files = _shipped_files()
    assert len(files) > 150, f"only {len(files)} files found: the scope has drifted"


def test_every_shipped_file_declares_its_licence() -> None:
    sans: list[str] = []
    for file in _shipped_files():
        # In the first lines: after a possible shebang, before the docstring.
        tete = "\n".join(file.read_text(encoding="utf-8").splitlines()[:5])
        if MARQUE not in tete:
            sans.append(str(file.relative_to(ROOT)))
    assert not sans, (
        f"{len(sans)} distributed file(s) without `{MARQUE}` at the top — add the line "
        f"(`# …` in Python, `// …` in TypeScript):\n  " + "\n  ".join(sans[:20])
    )


def test_no_file_declares_another_licence() -> None:
    """An `SPDX-License-Identifier` other than Apache-2.0 flags code that came from elsewhere.

    This is the case that really matters: not the omission, which shows, but the file that openly
    declares an incompatible license and that nobody reads.
    """
    etrangers: list[str] = []
    for file in _shipped_files():
        for line in file.read_text(encoding="utf-8").splitlines()[:5]:
            if "SPDX-License-Identifier" in line and "Apache-2.0" not in line:
                etrangers.append(f"{file.relative_to(ROOT)} : {line.strip()}")
    assert not etrangers, "this repository is entirely Apache-2.0:\n  " + "\n  ".join(etrangers)
