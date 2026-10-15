# SPDX-License-Identifier: Apache-2.0
"""Every file this repository DISTRIBUTES declares its license, in the file itself.

`LICENSE` and `NOTICE` state the repository's license. They do not follow a file that is copied
elsewhere — and that is precisely what happens in an open-core model with two entities
([ADR 0024](../../docs/adr/0024-deux-editions.md)): code flows between an Apache-2.0 repository
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

RACINE = pathlib.Path(__file__).resolve().parents[2]
MARQUE = "SPDX-License-Identifier: Apache-2.0"


def _fichiers_distribues() -> list[pathlib.Path]:
    trouves: list[pathlib.Path] = []
    racines = [
        *sorted((RACINE / "packages").glob("*/src")),
        *sorted((RACINE / "apps").glob("*/src")),
        RACINE / "apps/api/src/choregos_api/migrations",
    ]
    for racine in racines:
        if not racine.exists():
            continue
        trouves += [f for f in sorted(racine.rglob("*.py")) if "__pycache__" not in f.parts]
    for dossier in (RACINE / "apps/web/src", RACINE / "packages/contracts/ts"):
        if dossier.exists():
            trouves += [f for f in sorted(dossier.rglob("*")) if f.suffix in {".ts", ".tsx"}]
    return trouves


def test_il_y_a_bien_des_fichiers_a_verifier() -> None:
    """Without this guard, a path that changes would make the test green while looking at nothing."""
    fichiers = _fichiers_distribues()
    assert len(fichiers) > 150, f"only {len(fichiers)} files found: the scope has drifted"


def test_chaque_fichier_distribue_declare_sa_licence() -> None:
    sans: list[str] = []
    for fichier in _fichiers_distribues():
        # In the first lines: after a possible shebang, before the docstring.
        tete = "\n".join(fichier.read_text(encoding="utf-8").splitlines()[:5])
        if MARQUE not in tete:
            sans.append(str(fichier.relative_to(RACINE)))
    assert not sans, (
        f"{len(sans)} distributed file(s) without `{MARQUE}` at the top — add the line "
        f"(`# …` in Python, `// …` in TypeScript):\n  " + "\n  ".join(sans[:20])
    )


def test_aucun_fichier_ne_declare_une_autre_licence() -> None:
    """An `SPDX-License-Identifier` other than Apache-2.0 flags code that came from elsewhere.

    This is the case that really matters: not the omission, which shows, but the file that openly
    declares an incompatible license and that nobody reads.
    """
    etrangers: list[str] = []
    for fichier in _fichiers_distribues():
        for ligne in fichier.read_text(encoding="utf-8").splitlines()[:5]:
            if "SPDX-License-Identifier" in ligne and "Apache-2.0" not in ligne:
                etrangers.append(f"{fichier.relative_to(RACINE)} : {ligne.strip()}")
    assert not etrangers, "this repository is entirely Apache-2.0:\n  " + "\n  ".join(etrangers)
