# SPDX-License-Identifier: Apache-2.0
"""The one router every agents route group registers on (route order is the import order)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import StringConstraints

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]
AgentRef = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,62}(@[1-9][0-9]*)?$")]
