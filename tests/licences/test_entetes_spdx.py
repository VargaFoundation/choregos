# SPDX-License-Identifier: Apache-2.0
"""Chaque fichier que ce dépôt DISTRIBUE déclare sa licence, dans le fichier lui-même.

`LICENSE` et `NOTICE` disent la licence du dépôt. Ils ne suivent pas un fichier qu'on copie
ailleurs — et c'est précisément ce qui arrive dans un modèle open-core à deux entités
([ADR 0024](../../docs/adr/0024-deux-editions.md)) : du code circule entre un dépôt Apache-2.0 et
un dépôt propriétaire, dans les deux sens, et rien dans le fichier ne disait d'où il venait.

`SPDX-License-Identifier` le dit. Il est lisible par un humain en une ligne et par un outil
(`reuse`, les scanners d'entreprise, les générateurs de SBOM) sans configuration. Un fichier qui
arrive ici **sans** en-tête — collé d'ailleurs, ou copié depuis l'édition entreprise — fait donc
rougir la suite au lieu de se fondre dans l'arbre.

PORTÉE : ce qui est distribué. Les sources des paquets et des applications, les migrations, le
front, et les contrats TypeScript **générés** (leur bandeau vient du générateur, `gen_contracts.py`
— l'écrire à la main aurait fait rougir `contracts-check` au premier passage). Les tests ne sont pas
distribués ; ce fichier-ci porte quand même l'en-tête, par cohérence et parce qu'il coûte une ligne.
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
        RACINE / "apps/api/migrations",
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
    """Sans ce garde-fou, un chemin qui change rendrait le test vert en ne regardant rien."""
    fichiers = _fichiers_distribues()
    assert len(fichiers) > 150, f"seulement {len(fichiers)} fichiers trouvés : la portée a glissé"


def test_chaque_fichier_distribue_declare_sa_licence() -> None:
    sans: list[str] = []
    for fichier in _fichiers_distribues():
        # Dans les premières lignes : après un shebang éventuel, avant la docstring.
        tete = "\n".join(fichier.read_text(encoding="utf-8").splitlines()[:5])
        if MARQUE not in tete:
            sans.append(str(fichier.relative_to(RACINE)))
    assert not sans, (
        f"{len(sans)} fichier(s) distribué(s) sans `{MARQUE}` en tête — ajoutez la ligne "
        f"(`# …` en Python, `// …` en TypeScript) :\n  " + "\n  ".join(sans[:20])
    )


def test_aucun_fichier_ne_declare_une_autre_licence() -> None:
    """Un `SPDX-License-Identifier` autre qu'Apache-2.0 signale du code venu d'ailleurs.

    C'est le cas qui compte vraiment : pas l'oubli, qui se voit, mais le fichier qui déclare
    franchement une licence incompatible et que personne ne lit.
    """
    etrangers: list[str] = []
    for fichier in _fichiers_distribues():
        for ligne in fichier.read_text(encoding="utf-8").splitlines()[:5]:
            if "SPDX-License-Identifier" in ligne and "Apache-2.0" not in ligne:
                etrangers.append(f"{fichier.relative_to(RACINE)} : {ligne.strip()}")
    assert not etrangers, "ce dépôt est intégralement Apache-2.0 :\n  " + "\n  ".join(etrangers)
