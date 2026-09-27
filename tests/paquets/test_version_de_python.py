"""Les images tournent-elles sur la version de Python que les paquets exigent ?

Le 2026-09-27, un robot a proposé de passer les Dockerfiles de `python:3.12-slim` à
`python:3.14-slim`. Les dix `pyproject.toml` déclarent `requires-python = ">=3.12,<3.13"`, donc
l'image aurait embarqué un interpréteur que `uv sync` refuse. L'échec existait — le job d'images
rougissait — mais il parlait de résolution de dépendances, pas de la ligne `FROM` qui l'avait
causé. Une heure pour comprendre, et la même heure à chaque proposition suivante.

Ce test attrape la dérive **dans les deux sens** : une image qui avance sans les paquets, comme
des paquets qui avancent sans l'image — le second cas étant le plus sournois, parce que tout
passe en local et que seule la production voit la différence.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

RACINE = pathlib.Path(__file__).resolve().parents[2]
DOCKER = RACINE / "docker"
#: `FROM python:3.12-slim-bookworm AS builder`
IMAGE = re.compile(r"^FROM python:(\d+\.\d+)[-\w.]*\s", re.M)
#: `requires-python = ">=3.12,<3.13"`
EXIGENCE = re.compile(r">=\s*(\d+\.\d+)")


def _exigee() -> str:
    """La version plancher qu'exigent les paquets, et la garantie qu'ils sont d'accord."""
    fichiers = [
        RACINE / "pyproject.toml",
        *sorted(RACINE.glob("packages/*/pyproject.toml")),
        *sorted(RACINE.glob("apps/*/pyproject.toml")),
    ]
    versions: dict[str, list[str]] = {}
    for chemin in fichiers:
        contrainte = tomllib.loads(chemin.read_text(encoding="utf-8"))["project"].get("requires-python")
        assert contrainte, f"{chemin.relative_to(RACINE)} ne déclare pas `requires-python`"
        trouve = EXIGENCE.search(contrainte)
        assert trouve, f"{chemin.relative_to(RACINE)} : `{contrainte}` sans plancher lisible"
        versions.setdefault(trouve.group(1), []).append(str(chemin.relative_to(RACINE)))
    assert len(versions) == 1, "les paquets n'exigent pas tous la même version de Python : " + " ; ".join(
        f"{v} → {', '.join(f)}" for v, f in sorted(versions.items())
    )
    return next(iter(versions))


def _images() -> dict[str, list[str]]:
    """{version: fichiers} pour chaque `FROM python:` des Dockerfiles."""
    trouves: dict[str, list[str]] = {}
    for chemin in sorted(DOCKER.glob("*.Dockerfile")):
        for version in IMAGE.findall(chemin.read_text(encoding="utf-8")):
            trouves.setdefault(version, []).append(chemin.name)
    return trouves


def test_il_y_a_bien_des_images_a_verifier() -> None:
    """Sans cette garde, renommer les Dockerfiles rendrait la suite verte et vide."""
    assert _images(), f"aucun `FROM python:` trouvé dans {DOCKER.relative_to(RACINE)}"


def test_les_images_utilisent_la_version_exigee_par_les_paquets() -> None:
    exigee = _exigee()
    ecarts = {v: f for v, f in _images().items() if v != exigee}
    assert not ecarts, (
        f"les paquets exigent Python {exigee}, les images disent "
        + " ; ".join(f"{v} ({', '.join(sorted(set(f)))})" for v, f in sorted(ecarts.items()))
        + ". Monter l'un sans l'autre donne une image où `uv sync` refuse l'interpréteur — et "
        "l'erreur parle de dépendances, pas de la ligne `FROM` qui l'a causée."
    )
