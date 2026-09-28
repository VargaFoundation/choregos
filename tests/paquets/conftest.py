# SPDX-License-Identifier: Apache-2.0
"""La roue d'édition entreprise en miniature, partagée par les tests du modèle de paquet et de l'image.

Elle se construit avec `uv build`, comme le ferait la CI du dépôt privé : ce que ces tests éprouvent
est la roue qu'on publierait, pas un `.dist-info` écrit à la main.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

PYPROJECT = """\
[project]
name = "choregos-ee"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
# Le cœur N'EST PAS une dépendance : l'image EE est construite FROM l'image CE.
dependencies = []

[project.entry-points."choregos.plugins"]
choregos-ee = "choregos_ee:brancher"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""

MODULE = '''\
"""Édition entreprise, en miniature : elle se déclare, et rien d'autre."""

from __future__ import annotations


def brancher() -> None:
    # L'import est DANS la fonction : une roue EE importée trop tôt casserait sur un cœur absent.
    from choregos_api.edition import ENTREPRISE, declarer

    declarer(ENTREPRISE, fonctions=frozenset({"multi_org"}))
'''


@pytest.fixture
def roue_ee(tmp_path: pathlib.Path) -> pathlib.Path:
    """Construit une vraie roue avec `uv build`, comme le ferait la CI du dépôt privé."""
    source = tmp_path / "src" / "choregos_ee"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text(MODULE, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")

    fait = subprocess.run(
        ["uv", "build", "--wheel", "-o", "dist"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert fait.returncode == 0, f"la roue ne se construit pas :\n{fait.stderr[-800:]}"
    roues = list((tmp_path / "dist").glob("*.whl"))
    assert len(roues) == 1, roues
    return roues[0]
