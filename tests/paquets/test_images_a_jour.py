"""Does every image apply Debian's security fixes when it is built?

The images start from tags (`python:3.12-slim-bookworm`, `node:24-bookworm-slim`) that their
maintainers rebuild at their own pace. On 2026-10-06, `perl-base` 5.36.0-7+deb12u3 carried
three CRITICALs already fixed in `bookworm-security`: the release's Trivy scan rejected four
images out of five, and 0.16.0 did not ship. The runner passed — by luck: `build-essential`
pulled in `perl`, and with it the fixed `perl-base`.

The rule: every stage that ends up in an image — it starts from an external image and is not
merely the source of a `COPY --from` — runs `apt-get upgrade`. A stage that starts from another
stage of the same file inherits it.
"""

from __future__ import annotations

import pathlib
import re

RACINE = pathlib.Path(__file__).resolve().parents[2]
DOCKER = RACINE / "docker"
ETAPE = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", re.M | re.I)
SOURCE = re.compile(r"COPY\s+--from=(\S+)")
MISE_A_JOUR = re.compile(r"apt-get\s+upgrade\s+-y")


def _etapes_finales() -> list[tuple[str, str, str]]:
    """(file, stage, body) of every stage that ends up in an image and starts from an external one."""
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
                continue  # it inherits from the stage it starts from
            if nom in sources and nom not in bases_internes:
                continue  # a build stage: only what is copied from it is kept
            finales.append((chemin.name, nom, corps))
    return finales


def test_il_y_a_bien_des_images_a_verifier() -> None:
    """Without this guard, renaming the stages would leave the suite green and empty."""
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
    assert not sans, f"final stages without `apt-get upgrade -y`: {sans}"
