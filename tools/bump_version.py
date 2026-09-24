#!/usr/bin/env python3
"""Pose UNE version partout — ou vérifie qu'elle y est déjà.

    uv run python tools/bump_version.py 0.4.0          # écrit
    uv run python tools/bump_version.py 0.4.0 --check  # vérifie (la release le fait)

Ce que « partout » veut dire : les neuf `pyproject.toml`, le `Chart.yaml` de l'umbrella
(sa version, son appVersion, la version de chacune de ses dépendances), le `Chart.yaml` de
chaque sous-chart, et les tags d'images (`imageTag` global, `image.tag` des sous-charts).

Avant le 2026-09-24 rien ne faisait ça : les pyproject restaient à 0.1.0, le chart à
1.0.0, et `release.yml` réécrivait `imageTag` par `sed` au moment du build — le dépôt
contenait donc en permanence des versions mensongères. Ici, la version se pose AVANT le
tag, et la release refuse un tag qui ne correspond pas aux fichiers.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

RACINE = pathlib.Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


def _fichiers() -> list[tuple[pathlib.Path, list[tuple[re.Pattern[str], str]]]]:
    """(fichier, [(motif, remplacement-avec-\\g<1>VERSION)])."""
    cibles: list[tuple[pathlib.Path, list[tuple[re.Pattern[str], str]]]] = []
    for pyproject in sorted(RACINE.glob("**/pyproject.toml")):
        if ".venv" in pyproject.parts or "node_modules" in pyproject.parts:
            continue
        texte = pyproject.read_text(encoding="utf-8")
        if not re.search(r'^name = "choregos', texte, re.M):
            continue
        cibles.append((pyproject, [(re.compile(r'^(version = ")[^"]+(")', re.M), r"\g<1>{v}\g<2>")]))
    umbrella = RACINE / "charts" / "choregos" / "Chart.yaml"
    cibles.append(
        (
            umbrella,
            [
                (re.compile(r"^(version: ).*$", re.M), r"\g<1>{v}"),
                (re.compile(r"^(appVersion: ).*$", re.M), r'\g<1>"{v}"'),
                (re.compile(r"^(    version: ).*$", re.M), r"\g<1>{v}"),
            ],
        )
    )
    for chart in sorted((RACINE / "charts" / "choregos" / "charts").glob("*/Chart.yaml")):
        cibles.append(
            (
                chart,
                [
                    (re.compile(r"^(version: ).*$", re.M), r"\g<1>{v}"),
                    (re.compile(r"^(appVersion: ).*$", re.M), r'\g<1>"{v}"'),
                ],
            )
        )
    valeurs = RACINE / "charts" / "choregos" / "values.yaml"
    cibles.append(
        (
            valeurs,
            [
                (re.compile(r'^(  imageTag: ").*(")$', re.M), r"\g<1>{v}\g<2>"),
                (re.compile(r'^(    tag: ")\d[^"]*(")$', re.M), r"\g<1>{v}\g<2>"),
            ],
        )
    )
    return cibles


def appliquer(version: str, *, check: bool) -> int:
    ecarts: list[str] = []
    for fichier, motifs in _fichiers():
        avant = fichier.read_text(encoding="utf-8")
        apres = avant
        for motif, remplacement in motifs:
            apres = motif.sub(remplacement.replace("{v}", version), apres)
        if apres != avant:
            if check:
                ecarts.append(str(fichier.relative_to(RACINE)))
            else:
                fichier.write_text(apres, encoding="utf-8")
                print(f"{fichier.relative_to(RACINE)} → {version}")
    if check and ecarts:
        print(f"version {version} absente de : {', '.join(ecarts)}", file=sys.stderr)
        return 1
    if check:
        print(f"version {version} partout")
    elif any("Chart.yaml" in str(f) for f, _ in _fichiers()):
        print("→ puis : helm dependency update charts/choregos  (Chart.lock)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version")
    parser.add_argument("--check", action="store_true", help="ne rien écrire, échouer si un fichier diverge")
    args = parser.parse_args()
    if not SEMVER.match(args.version):
        parser.error(f"version semver attendue, reçu {args.version!r}")
    return appliquer(args.version, check=args.check)


if __name__ == "__main__":
    sys.exit(main())
