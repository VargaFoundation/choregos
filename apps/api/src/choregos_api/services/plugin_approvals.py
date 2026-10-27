# SPDX-License-Identifier: Apache-2.0
"""The second approval of a plugin that brings a server (ADR 0093 §4, ADR 0050, S43-05).

A plugin that brings a server runs code the organisation did not write, with the secrets an
administrator gives it: one administrator's word is not enough, as for a high-risk skill (S32-06). An
install or upgrade of such a plugin is therefore checked in full (the installer's `check`, nothing
written), then RECORDED here at the exact version and content digest that was checked, and the route
answers 202. Nothing is installed until an administrator other than the requester approves; the plugin
is then obtained again, and installed only if the source still serves the same digest.

A default plugin (S43-05's start-up reconcile) is requested by the platform (`requested_by` is `None`):
any administrator of the organisation is then the second person, the operator who listed it being the
first.
"""

from __future__ import annotations

from typing import Any

from choregos_core.plugins import Plugin
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.base import utcnow
from ..db.models import OrgPluginApproval
from ..errors import conflict, forbidden, not_found
from ..rbac import SYSTEM, Principal
from ..schemas.plugins import PluginApprovalDto

INSTALL = "install"
UPGRADE = "upgrade"
PENDING = "pending"
APPROVED = "approved"


async def request(
    session: AsyncSession,
    org_id: str,
    action: str,
    source: str,
    plugin: Plugin,
    reasons: tuple[str, ...],
    principal: Principal | None,
    *,
    default: bool = False,
) -> tuple[OrgPluginApproval, bool]:
    """The pending request for this plugin's content in this organisation, recorded once: asking again
    for the same digest answers the request already waiting. `(row, created)`."""
    found = (
        await session.execute(
            select(OrgPluginApproval).where(
                OrgPluginApproval.org_id == org_id,
                OrgPluginApproval.name == plugin.name,
                OrgPluginApproval.digest == plugin.digest,
                OrgPluginApproval.action == action,
                OrgPluginApproval.status == PENDING,
            )
        )
    ).scalar_one_or_none()
    if found is not None:
        return found, False
    row = OrgPluginApproval(
        org_id=org_id,
        action=action,
        source=source,
        name=plugin.name,
        version=plugin.version,
        digest=plugin.digest,
        reason="; ".join(reasons),
        is_default=default,
        requested_by=principal.email if principal is not None else None,
        status=PENDING,
    )
    session.add(row)
    await session.flush()
    await record(session, principal or SYSTEM, "plugin.approval.request", org_id=org_id,
                 target_type="plugin", target_id=plugin.name, request=action, version=plugin.version,
                 digest=plugin.digest, reason=row.reason, default=default)  # fmt: skip
    return row, True


async def pending_install(session: AsyncSession, org_id: str, name: str) -> OrgPluginApproval | None:
    """A request to install `name` still waiting, whatever its version."""
    return (
        (
            await session.execute(
                select(OrgPluginApproval).where(
                    OrgPluginApproval.org_id == org_id,
                    OrgPluginApproval.name == name,
                    OrgPluginApproval.action == INSTALL,
                    OrgPluginApproval.status == PENDING,
                )
            )
        )
        .scalars()
        .first()
    )


async def listed(session: AsyncSession, org_id: str) -> list[OrgPluginApproval]:
    rows = await session.execute(
        select(OrgPluginApproval)
        .where(OrgPluginApproval.org_id == org_id)
        .order_by(OrgPluginApproval.created_at.desc(), OrgPluginApproval.id)
        .limit(200)
    )
    return list(rows.scalars())


async def pending_one(session: AsyncSession, org_id: str, approval_id: str) -> OrgPluginApproval:
    row = (
        await session.execute(
            select(OrgPluginApproval).where(
                OrgPluginApproval.org_id == org_id, OrgPluginApproval.id == approval_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Plugin approval", approval_id)
    if row.status != PENDING:
        raise conflict(f"the request to {row.action} `{row.name}` {row.version} is already {row.status}")
    return row


def check_second_person(row: OrgPluginApproval, principal: Principal) -> None:
    """The requester does not approve their own request (ADR 0050)."""
    if row.requested_by and row.requested_by.lower() == principal.email.lower():
        raise forbidden(
            f"you asked to {row.action} `{row.name}` {row.version}: a second administrator approves a "
            "plugin that brings a server"
        )


async def mark_approved(session: AsyncSession, row: OrgPluginApproval, principal: Principal) -> None:
    row.status, row.decided_by, row.decided_at = APPROVED, principal.email, utcnow()
    await session.flush()
    await record(session, principal, "plugin.approve", org_id=row.org_id, target_type="plugin",
                 target_id=row.name, request=row.action, version=row.version, digest=row.digest,
                 requested_by=row.requested_by, default=row.is_default)  # fmt: skip


async def forget(session: AsyncSession, org_id: str, name: str) -> None:
    """The requests still waiting for a plugin that is uninstalled: there is nothing left to upgrade."""
    await session.execute(
        delete(OrgPluginApproval).where(
            OrgPluginApproval.org_id == org_id,
            OrgPluginApproval.name == name,
            OrgPluginApproval.status == PENDING,
        )
    )


def to_dto(row: OrgPluginApproval) -> PluginApprovalDto:
    return PluginApprovalDto(
        id=row.id,
        action=row.action,
        source=row.source,
        name=row.name,
        version=row.version,
        digest=row.digest,
        reason=row.reason,
        default=row.is_default,
        requested_by=row.requested_by,
        status=row.status,
        decided_by=row.decided_by,
        created_at=row.created_at,
        decided_at=row.decided_at,
    )


def pending_body(row: OrgPluginApproval) -> dict[str, Any]:
    """The contract's `PluginInstallPending`."""
    return {"status": "pending_approval", "approval_id": row.id, "reason": row.reason}


__all__ = [
    "APPROVED",
    "INSTALL",
    "PENDING",
    "UPGRADE",
    "check_second_person",
    "forget",
    "listed",
    "mark_approved",
    "pending_body",
    "pending_install",
    "pending_one",
    "request",
    "to_dto",
]
