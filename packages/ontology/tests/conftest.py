# SPDX-License-Identifier: Apache-2.0
"""Shared helpers: a copy of the reference package that a test may mutate."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CORE_REF = HERE / "fixtures" / "core-ref"
SNAPSHOTS = HERE / "snapshots"


@pytest.fixture
def core_ref(tmp_path: Path) -> Path:
    target = tmp_path / "core-ref"
    shutil.copytree(CORE_REF, target)
    return target


Mutate = Callable[[str, str, str], None]


@pytest.fixture
def mutate(core_ref: Path) -> Mutate:
    """Replace ``old`` by ``new`` in one file of the copy; fails loudly if ``old`` is absent."""

    def apply(file: str, old: str, new: str) -> None:
        path = core_ref / file
        text = path.read_text(encoding="utf-8")
        assert old in text, f"{old!r} not found in {file}"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    return apply
