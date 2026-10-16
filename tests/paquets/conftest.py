# SPDX-License-Identifier: Apache-2.0
"""The enterprise-edition wheel in miniature, shared by the package-model and image tests.

It is built with `uv build`, as the private repository's CI would: what these tests exercise is
the wheel that would be published, not a hand-written `.dist-info`.
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
# The core is NOT a dependency: the EE image is built FROM the CE image.
dependencies = []

[project.entry-points."choregos.plugins"]
choregos-ee = "choregos_ee:brancher"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""

MODULE = '''\
"""Enterprise edition in miniature: it declares itself, and nothing else."""

from __future__ import annotations


def brancher() -> None:
    # The import is INSIDE the function: an EE wheel imported too early would break on a missing core.
    from choregos_api.edition import ENTREPRISE, declarer

    declarer(ENTREPRISE, fonctions=frozenset({"multi_org"}))
'''


@pytest.fixture
def roue_ee(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build a real wheel with `uv build`, as the private repository's CI would."""
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
    assert fait.returncode == 0, f"the wheel does not build:\n{fait.stderr[-800:]}"
    roues = list((tmp_path / "dist").glob("*.whl"))
    assert len(roues) == 1, roues
    return roues[0]
