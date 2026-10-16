"""Do the images run on the Python version the packages require?

On 2026-09-27, a bot proposed moving the Dockerfiles from `python:3.12-slim` to
`python:3.14-slim`. The ten `pyproject.toml` files declare `requires-python = ">=3.12,<3.13"`,
so the image would have shipped an interpreter that `uv sync` refuses. The failure existed — the
images job went red — but it talked about dependency resolution, not about the `FROM` line that
had caused it. An hour to understand, and the same hour for every following proposal.

This test catches drift **in both directions**: an image that moves ahead of the packages, as
well as packages that move ahead of the image — the second case being the sneakier, because
everything passes locally and only production sees the difference.
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
    """The floor version the packages require, and the guarantee that they agree."""
    fichiers = [
        RACINE / "pyproject.toml",
        *sorted(RACINE.glob("packages/*/pyproject.toml")),
        *sorted(RACINE.glob("apps/*/pyproject.toml")),
    ]
    versions: dict[str, list[str]] = {}
    for chemin in fichiers:
        contrainte = tomllib.loads(chemin.read_text(encoding="utf-8"))["project"].get("requires-python")
        assert contrainte, f"{chemin.relative_to(RACINE)} does not declare `requires-python`"
        trouve = EXIGENCE.search(contrainte)
        assert trouve, f"{chemin.relative_to(RACINE)}: `{contrainte}` has no readable floor"
        versions.setdefault(trouve.group(1), []).append(str(chemin.relative_to(RACINE)))
    assert len(versions) == 1, "the packages do not all require the same Python version: " + " ; ".join(
        f"{v} → {', '.join(f)}" for v, f in sorted(versions.items())
    )
    return next(iter(versions))


def _images() -> dict[str, list[str]]:
    """{version: files} for each `FROM python:` of the Dockerfiles."""
    trouves: dict[str, list[str]] = {}
    for chemin in sorted(DOCKER.glob("*.Dockerfile")):
        for version in IMAGE.findall(chemin.read_text(encoding="utf-8")):
            trouves.setdefault(version, []).append(chemin.name)
    return trouves


def test_il_y_a_bien_des_images_a_verifier() -> None:
    """Without this guard, renaming the Dockerfiles would leave the suite green and empty."""
    assert _images(), f"no `FROM python:` found in {DOCKER.relative_to(RACINE)}"


def test_les_images_utilisent_la_version_exigee_par_les_paquets() -> None:
    exigee = _exigee()
    ecarts = {v: f for v, f in _images().items() if v != exigee}
    assert not ecarts, (
        f"the packages require Python {exigee}, the images say "
        + " ; ".join(f"{v} ({', '.join(sorted(set(f)))})" for v, f in sorted(ecarts.items()))
        + ". Moving one without the other gives an image where `uv sync` refuses the interpreter — "
        "and the error talks about dependencies, not about the `FROM` line that caused it."
    )
