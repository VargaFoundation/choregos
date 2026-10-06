"""Chaque image applique-t-elle les correctifs de sécurité de Debian à sa construction ?

Les images partent d'étiquettes (`python:3.12-slim-bookworm`, `node:22-bookworm-slim`) que leurs
mainteneurs reconstruisent à leur rythme. Le 2026-10-06, `perl-base` 5.36.0-7+deb12u3 y portait
trois CRITICAL déjà corrigés dans `bookworm-security` : le scan Trivy de la release a refusé quatre
images sur cinq, et la 0.16.0 n'est pas sortie. Le runner passait — par chance : `build-essential`
tirait `perl`, et avec lui le `perl-base` corrigé.

La règle : toute étape qui finit dans une image — elle part d'une image extérieure et n'est pas
seulement la source d'un `COPY --from` — fait `apt-get upgrade`. Une étape qui part d'une autre
étape du même fichier en hérite.
"""

from __future__ import annotations

import pathlib
import re

RACINE = pathlib.Path(__file__).resolve().parents[2]
DOCKER = RACINE / "docker"
ETAPE = re.compile(r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?\s*$", re.M | re.I)
SOURCE = re.compile(r"COPY\s+--from=(\S+)")
MISE_A_JOUR = re.compile(r"apt-get\s+upgrade\s+-y")


def _etapes_finales() -> list[tuple[str, str, str]]:
    """(fichier, étape, corps) de chaque étape qui finit dans une image et part d'une image extérieure."""
    finales: list[tuple[str, str, str]] = []
    for chemin in sorted(DOCKER.glob("*.Dockerfile")):
        texte = chemin.read_text(encoding="utf-8")
        debuts = list(ETAPE.finditer(texte))
        etapes = []
        for i, debut in enumerate(debuts):
            fin = debuts[i + 1].start() if i + 1 < len(debuts) else len(texte)
            etapes.append((debut.group(2) or f"#{i}", debut.group(1), texte[debut.end() : fin]))
        noms = {nom for nom, _, _ in etapes}
        bases_internes = {base for _, base, _ in etapes if base in noms}
        sources = set(SOURCE.findall(texte))
        for nom, base, corps in etapes:
            if base in noms:
                continue  # elle hérite de l'étape dont elle part
            if nom in sources and nom not in bases_internes:
                continue  # une étape de construction : on n'en garde que ce qu'on copie
            finales.append((chemin.name, nom, corps))
    return finales


def test_il_y_a_bien_des_images_a_verifier() -> None:
    """Sans cette garde, renommer les étapes rendrait la suite verte et vide."""
    vues = {(fichier, etape) for fichier, etape, _ in _etapes_finales()}
    assert {
        ("api.Dockerfile", "commun"),
        ("tools.Dockerfile", "runtime"),
        ("web.Dockerfile", "runtime"),
        ("runner.Dockerfile", "runtime"),
        ("base.Dockerfile", "base"),
    } <= vues, vues


def test_chaque_image_applique_les_correctifs_de_debian() -> None:
    finales = _etapes_finales()
    sans = [f"{fichier} / {etape}" for fichier, etape, corps in finales if not MISE_A_JOUR.search(corps)]
    assert not sans, f"étapes finales sans `apt-get upgrade -y` : {sans}"
