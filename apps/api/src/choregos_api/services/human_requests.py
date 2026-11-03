# SPDX-License-Identifier: Apache-2.0
"""The one constructor of human requests: every row carries a due date."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from choregos_core import utcnow

from ..db.models import HumanRequest

DEFAULT_SLA_HOURS = 24


def new_human_request(
    *,
    work_item_id: str,
    project_id: str,
    transition_id: str | None,
    kind: str,
    payload: dict[str, Any],
    sla_hours: int | None = None,
) -> HumanRequest:
    """Build a human request with `requested_at` now and `due_at` after the SLA (24 h by default).

    This is the one place a `HumanRequest` is constructed; the caller adds the row to its session.
    """
    requested_at = utcnow()
    return HumanRequest(
        work_item_id=work_item_id,
        project_id=project_id,
        transition_id=transition_id,
        kind=kind,
        payload=payload,
        requested_at=requested_at,
        due_at=requested_at + timedelta(hours=sla_hours or DEFAULT_SLA_HOURS),
    )
