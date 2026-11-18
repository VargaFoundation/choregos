# SPDX-License-Identifier: Apache-2.0
"""The one router every knowledge route group registers on (route order is the import order)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

router = APIRouter(tags=["knowledge"])

Slug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]
