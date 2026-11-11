# SPDX-License-Identifier: Apache-2.0
"""The standing delegation of a task (ADR 0137 points 3-4, S63-14).

A person agrees, once, that a `delegation: read_only` task reads on their behalf while they are away. The
agreement is the EXACT list of needs (with their operations) the manifest's task declares: `POST` without
`agree_to` equal to it is a 409 that shows it. Read-only only: a task that lists a write need cannot be
delegated, and a write attempted under a delegation is a typed refusal (`write_under_delegation`): a task
has no conversation to confirm in, so there is no proposal to make (S63-10's `conversation_actions` belong
to a conversation).

What a delegation holds: the needs, the expiry (90 days by default, capped by the installation's
`policy_overrides.delegation.max_days`), the digest of the task's needs in the manifest installed at
agreement, and, when a `user_exchange` need is read, an OFFLINE token of the identity provider
(`offline_access`) sealed at `user/<id>/delegation/<delegation id>`. A delegated read draws a token from it
per call exactly as a conversation's does (`identity_exchange`), a `user_consent` need uses the person's
connected account, and each read is journaled with the delegation's id.

`check` is the one place a delegation is judged, at every read and every scheduled run, from the clock and
from the rows: revoked, expired, the person left the audience or the organisation, or the digest of the
task's needs changed (a new agreement is then needed) all refuse it and mark it `suspended`, with the
reason. Nothing is cached.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import structlog
from choregos_contracts import Role
from choregos_core import utcnow
from choregos_core.secrets import SecretNotFound
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record as audit
from ..db.models import (
    AssistantInstallation,
    AssistantRead,
    AssistantToolBinding,
    ConnectedAccount,
    Delegation,
    Membership,
    Organization,
    OrgConnector,
    TaskSubscription,
    User,
)
from ..errors import ApiError, conflict, not_found, unprocessable
from ..rbac import Permission, Principal
from ..schemas.delegations import Delegation as DelegationView
from ..schemas.delegations import DelegationCreate, DelegationNeed
from ..secret_store import SecretStoreUnavailable, get_secret_store
from . import assistants
from .assistant_access import holds
from .assistant_conversations import audience_installation
from .assistant_erasure import USER_ERASERS
from .assistant_memory import subjects_in
from .connected_accounts import CONNECTED, connect_url
from .identity_exchange import (
    SERVICE,
    USER_CONSENT,
    USER_EXCHANGE,
    Acting,
    IdentityUnavailable,
    issue_offline_token,
    revoke_offline_token,
)

log = structlog.get_logger("choregos.assistant_delegations")

DEFAULT_DAYS: Final = 90
ACTIVE: Final = "active"
SUSPENDED: Final = "suspended"
REVOKED: Final = "revoked"
EXPIRED: Final = "expired"


class DelegationRefused(ValueError):  # noqa: N818 - a named refusal, rendered as a typed reason
    """A delegated read or write the delegation does not allow. `code` is the typed reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def secret_name(user_id: str, delegation_id: str) -> str:
    return f"user/{user_id}/delegation/{delegation_id}"


# -- the agreement -----------------------------------------------------------------------------------------


def declared_task(document: dict[str, Any] | None, task: str) -> dict[str, Any] | None:
    return next(
        (t for t in (document or {}).get("tasks") or [] if isinstance(t, dict) and t.get("name") == task),
        None,
    )


def agreement_of(document: dict[str, Any], declared: dict[str, Any]) -> list[dict[str, Any]]:
    """The needs the task reads, each with its operations, access and identity, as the manifest says."""
    tools = {str(t["need"]): t for t in document.get("tools") or [] if isinstance(t, dict) and "need" in t}
    found: list[dict[str, Any]] = []
    for need in declared.get("needs") or []:
        tool = tools.get(str(need))
        if tool is None:
            raise unprocessable(f"the task lists the need `{need}`, which the assistant does not declare")
        found.append(
            {
                "need": str(need),
                "operations": [str(o) for o in tool.get("operations") or []],
                "access": str(tool.get("access") or "read"),
                "identity": str(tool.get("identity") or SERVICE),
            }
        )
    return found


def digest_of(task: str, agreement: list[dict[str, Any]]) -> str:
    """The digest of what a person agreed to: the task and its needs with operations, access and identity."""
    canonical = json.dumps(
        {"task": task, "needs": sorted(agreement, key=lambda n: n["need"])},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def max_days_of(row: AssistantInstallation) -> int:
    declared = ((row.policy_overrides or {}).get("delegation") or {}).get("max_days")
    if isinstance(declared, int) and not isinstance(declared, bool) and declared >= 1:
        return declared
    return DEFAULT_DAYS


def _utc(moment: datetime) -> datetime:
    """SQLite hands datetimes back naive: they were stored in UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def status_of(row: Delegation, now: datetime) -> str:
    if row.revoked_at is not None or row.status == REVOKED:
        return REVOKED
    if _utc(row.expires_at) <= now:
        return EXPIRED
    return row.status


# -- grant -------------------------------------------------------------------------------------------------


async def _bound(session: AsyncSession, installation_id: str) -> dict[str, tuple[AssistantToolBinding, str]]:
    rows = await session.execute(
        select(AssistantToolBinding, OrgConnector.name)
        .join(OrgConnector, OrgConnector.id == AssistantToolBinding.connector_id)
        .where(AssistantToolBinding.installation_id == installation_id)
    )
    return {b.need: (b, name) for b, name in rows.all()}


async def _subscription(
    session: AsyncSession, row: AssistantInstallation, user_id: str, task: str
) -> TaskSubscription | None:
    return (
        await session.execute(
            select(TaskSubscription).where(
                TaskSubscription.installation_id == row.id,
                TaskSubscription.user_id == user_id,
                TaskSubscription.task == task,
            )
        )
    ).scalar_one_or_none()


def view_of(row: Delegation, org: str, assistant: str, now: datetime) -> DelegationView:
    return DelegationView(
        id=row.id,
        org=org,
        assistant=assistant,
        task=row.task,
        needs=[
            DelegationNeed(need=n["need"], operations=n["operations"], identity=n["identity"])
            for n in row.needs
        ],
        granted_at=row.granted_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        status=status_of(row, now),
        suspended_reason=row.suspended_reason,
        manifest_digest=row.manifest_digest,
        reminded_at=row.reminded_at,
    )


async def grant(
    session: AsyncSession, principal: Principal, body: DelegationCreate, *, now: datetime | None = None
) -> DelegationView:
    """`POST /me/delegations`. 404 outside the audience; 422 for a task that cannot be delegated; 409 with
    the needs when `agree_to` differs from them, when the installation is not ready, a need is unbound, a
    `user_consent` account is not connected, or the person has not subscribed to the task."""
    now = now or utcnow()
    organisation, row = await audience_installation(session, principal, body.org, body.assistant)
    document = await assistants.manifest(session, row)
    declared = declared_task(document, body.task)
    if document is None or declared is None:
        raise not_found("Task", body.task)
    if declared.get("delegation") != "read_only":
        raise unprocessable(f"the task `{body.task}` does not read under a delegation (`delegation: none`)")
    agreement = agreement_of(document, declared)
    writes = [n["need"] for n in agreement if n["access"] != "read"]
    if writes:
        raise unprocessable(
            f"a task that lists a write need cannot be delegated (a delegation is read-only): {writes}"
        )
    wanted = [n["need"] for n in agreement]
    if sorted(body.agree_to) != sorted(wanted):
        raise conflict(
            f"a delegation of `{body.task}` is the agreement to exactly these needs: {wanted}; "
            "send them in `agree_to` to agree",
            extra={"needs": agreement, "agree_to": wanted},
        )
    status, _ = await assistants.refresh(session, row)
    if status != assistants.READY:
        raise conflict(f"`{body.assistant}` is {status}: it cannot be delegated until it is ready")
    sub = await _subscription(session, row, principal.user_id, body.task)
    if sub is None:
        raise conflict(
            f"subscribe to the task `{body.task}` first: a delegation is linked to the subscription"
        )
    await _require_bound_and_connected(session, row, organisation, principal.user_id, agreement)
    days = min(body.expires_in_days or DEFAULT_DAYS, max_days_of(row))
    delegation = Delegation(
        org_id=row.org_id,
        installation_id=row.id,
        user_id=principal.user_id,
        task=body.task,
        needs=[
            {"need": n["need"], "operations": n["operations"], "identity": n["identity"]} for n in agreement
        ],
        granted_at=now,
        expires_at=now + timedelta(days=days),
        manifest_digest=digest_of(body.task, agreement),
        status=ACTIVE,
    )
    session.add(delegation)
    await session.flush()
    if any(n["identity"] == USER_EXCHANGE for n in agreement):
        await _seal_offline_token(session, row, delegation)
    previous = list(
        (
            await session.execute(
                select(Delegation).where(
                    Delegation.installation_id == row.id,
                    Delegation.user_id == principal.user_id,
                    Delegation.task == body.task,
                    Delegation.id != delegation.id,
                    Delegation.revoked_at.is_(None),
                )
            )
        ).scalars()
    )
    for old in previous:
        await _revoke(session, old, now)
    sub.delegation_id = delegation.id
    sub.last_refusal = None
    await audit(
        session,
        principal,
        "assistant.delegation.granted",
        org_id=row.org_id,
        target_type="delegation",
        target_id=delegation.id,
        organisation=organisation.slug,
        task=body.task,
        needs=wanted,
        expires_at=delegation.expires_at.isoformat(),
    )
    return view_of(delegation, organisation.slug, row.slug, now)


async def _require_bound_and_connected(
    session: AsyncSession,
    row: AssistantInstallation,
    organisation: Organization,
    user_id: str,
    agreement: list[dict[str, Any]],
) -> None:
    bound = await _bound(session, row.id)
    for need in agreement:
        entry = bound.get(need["need"])
        if entry is None or not entry[0].enabled:
            raise conflict(f"the need `{need['need']}` is not bound: the task cannot read it")
        binding, connector = entry
        if binding.identity != USER_CONSENT:
            continue
        account = (
            await session.execute(
                select(ConnectedAccount).where(
                    ConnectedAccount.org_id == row.org_id,
                    ConnectedAccount.user_id == user_id,
                    ConnectedAccount.connector == connector,
                    ConnectedAccount.status == CONNECTED,
                )
            )
        ).scalar_one_or_none()
        if account is None:
            url = connect_url(organisation.slug, connector)
            raise conflict(
                f"connect your `{connector}` account first: open {url}",
                extra={"code": "account_not_connected", "connect_url": url},
            )


async def _seal_offline_token(
    session: AsyncSession, row: AssistantInstallation, delegation: Delegation
) -> None:
    store = get_secret_store()
    if not store.available():
        raise conflict("the sealed secret store is not configured: an offline token cannot be kept")
    try:
        token = await issue_offline_token(session, row.org_id, delegation.user_id)
    except IdentityUnavailable as unavailable:
        raise unprocessable(str(unavailable)) from unavailable
    delegation.secret_ref = secret_name(delegation.user_id, delegation.id)
    await store.put(session, row.org_id, delegation.secret_ref, token, delegation.user_id)


# -- revoke ------------------------------------------------------------------------------------------------


async def _read_sealed(session: AsyncSession, delegation: Delegation) -> str | None:
    if not delegation.secret_ref:
        return None
    try:
        return (await get_secret_store().get_many(session, delegation.org_id, [delegation.secret_ref])).get(
            delegation.secret_ref
        )
    except (SecretNotFound, SecretStoreUnavailable):
        return None


async def _revoke(session: AsyncSession, delegation: Delegation, now: datetime) -> bool:
    """Revoked at once: the offline token is revoked at the provider when it has an endpoint, the sealed copy
    is deleted, the row says `revoked`. Returns whether the provider confirmed the revocation."""
    confirmed = False
    sealed = await _read_sealed(session, delegation)
    if sealed:
        confirmed = await revoke_offline_token(sealed)
    if delegation.secret_ref:
        try:
            await get_secret_store().delete(session, delegation.org_id, delegation.secret_ref)
        except SecretStoreUnavailable:
            log.warning("delegation.secret_not_deleted", delegation=delegation.id)
        delegation.secret_ref = None
    delegation.status = REVOKED
    delegation.revoked_at = now
    delegation.suspended_reason = None
    await session.flush()
    return confirmed


async def revoke(
    session: AsyncSession, principal: Principal, delegation_id: str, *, now: datetime | None = None
) -> None:
    """`DELETE /me/delegations/{id}`: the person's own delegation only (404 otherwise, even for an
    administrator)."""
    now = now or utcnow()
    delegation = await session.get(Delegation, delegation_id)
    if delegation is None or delegation.user_id != principal.user_id:
        raise not_found("Delegation", delegation_id)
    if delegation.revoked_at is None:
        await _revoke(session, delegation, now)
        organisation = await session.get(Organization, delegation.org_id)
        await audit(
            session,
            principal,
            "assistant.delegation.revoked",
            org_id=delegation.org_id,
            target_type="delegation",
            target_id=delegation.id,
            organisation=organisation.slug if organisation is not None else "",
            task=delegation.task,
        )


async def mine(
    session: AsyncSession, principal: Principal, *, now: datetime | None = None
) -> list[DelegationView]:
    """`GET /me/delegations`: the caller's, most recent first, with their expiry."""
    now = now or utcnow()
    rows = (
        await session.execute(
            select(Delegation, Organization.slug, AssistantInstallation.slug)
            .join(Organization, Organization.id == Delegation.org_id)
            .join(AssistantInstallation, AssistantInstallation.id == Delegation.installation_id)
            .where(Delegation.user_id == principal.user_id)
            .order_by(Delegation.granted_at.desc(), Delegation.id)
        )
    ).all()
    return [view_of(d, org, slug, now) for d, org, slug in rows]


# -- judging a delegation ----------------------------------------------------------------------------------


async def check(session: AsyncSession, delegation: Delegation, *, now: datetime | None = None) -> str | None:
    """Why the delegation cannot be used now, or `None`. A refused delegation is marked `suspended` with the
    reason (`revoked` stays `revoked`); one that holds again (the person returned to the audience) is
    `active` again. Judged from the rows and the clock, at every read and every scheduled run."""
    now = now or utcnow()
    if delegation.revoked_at is not None or delegation.status == REVOKED:
        return "the delegation was revoked"
    reason = await _reason(session, delegation, now)
    if reason is None:
        if delegation.status != ACTIVE:
            delegation.status, delegation.suspended_reason = ACTIVE, None
        return None
    delegation.status, delegation.suspended_reason = SUSPENDED, reason
    await session.flush()
    return reason


async def _reason(session: AsyncSession, delegation: Delegation, now: datetime) -> str | None:
    if _utc(delegation.expires_at) <= now:
        return "the delegation has expired: agree to it again"
    organisation = await session.get(Organization, delegation.org_id)
    row = await session.get(AssistantInstallation, delegation.installation_id)
    user = await session.get(User, delegation.user_id)
    if organisation is None or row is None or user is None:
        return "the person or the assistant no longer exists"
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.org_id == organisation.id,
                Membership.project_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        return "the person is no longer a member of the organisation (disabled or removed)"
    principal = Principal(
        user_id=user.id,
        email=user.email,
        org_roles={organisation.slug: Role(membership.role)},
        groups=list(user.groups or []),
    )
    if not holds(principal, Permission.ASSISTANT_USE, organisation.slug, row):
        return "the person is no longer in the assistant's audience"
    document = await assistants.manifest(session, row)
    declared = declared_task(document, delegation.task)
    if document is None or declared is None or declared.get("delegation") != "read_only":
        return "the task is no longer a delegated task of the assistant: agree to it again"
    try:
        digest = digest_of(delegation.task, agreement_of(document, declared))
    except ApiError:  # a need the manifest no longer declares is a changed agreement
        return "the needs the task reads changed since you agreed: agree to them again"
    if digest != delegation.manifest_digest:
        return "the needs the task reads changed since you agreed: agree to them again"
    return None


# -- reading under it --------------------------------------------------------------------------------------


def acting_for(
    delegation: Delegation, row: AssistantInstallation, user: User, binding: AssistantToolBinding
) -> Acting | None:
    """Who a delegated read of this need is: `None` (the connector's own identity) for a `service` need, the
    person through the delegation's offline token for `user_exchange`, through their connected account for
    `user_consent`."""
    actor = f"assistant:{row.assistant_name}"
    subject = user.oidc_sub or user.id
    if binding.identity == USER_EXCHANGE:
        return Acting(
            USER_EXCHANGE,
            user_id=user.id,
            subject=subject,
            org_id=delegation.org_id,
            actor=actor,
            refresh_secret=delegation.secret_ref or "",
        )
    if binding.identity == USER_CONSENT:
        return Acting(USER_CONSENT, user_id=user.id, subject=subject, org_id=delegation.org_id, actor=actor)
    return None


async def guard_read(
    session: AsyncSession,
    delegation: Delegation,
    row: AssistantInstallation,
    *,
    need: str,
    access: str,
    binding: AssistantToolBinding,
    now: datetime | None = None,
) -> Acting | None:
    """Everything a delegated read must satisfy before it reaches a source; the identity to call under.
    `DelegationRefused` says which rule refused it."""
    if access != "read":
        raise DelegationRefused(
            "write_under_delegation",
            "a delegation is read-only and a task has no conversation to confirm a write in: "
            "the write is not made",
        )
    reason = await check(session, delegation, now=now)
    if reason is not None:
        raise DelegationRefused("delegation_refused", reason)
    if need not in {n["need"] for n in delegation.needs}:
        raise DelegationRefused("outside_agreement", f"the need `{need}` is not part of what you agreed to")
    user = await session.get(User, delegation.user_id)
    if user is None:
        raise DelegationRefused("delegation_refused", "the person no longer exists")
    return acting_for(delegation, row, user, binding)


async def journal(
    session: AsyncSession,
    delegation: Delegation,
    *,
    need: str,
    operation: str,
    result: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """One line of the reads journal for a delegated read: the need, the operation, the subject the result
    carries, the delegation's id; never the content."""
    found = subjects_in(result.get("structuredContent"))
    session.add(
        AssistantRead(
            org_id=delegation.org_id,
            installation_id=delegation.installation_id,
            user_id=delegation.user_id,
            need=need,
            operation=operation,
            subject_type=found[0]["type"] if found else None,
            subject_key=found[0]["key"] if found else None,
            delegation_id=delegation.id,
            at=now or utcnow(),
        )
    )


# -- the reminder before expiry ----------------------------------------------------------------------------

REMIND_DAYS: Final = 7


def remind_days_of(row: AssistantInstallation) -> int:
    """How many days before the end the person is told: `policy_overrides.delegation.remind_days`, else 7."""
    declared = ((row.policy_overrides or {}).get("delegation") or {}).get("remind_days")
    if isinstance(declared, int) and not isinstance(declared, bool) and declared >= 1:
        return declared
    return REMIND_DAYS


async def remind_expiring(session: AsyncSession, now: datetime | None = None) -> int:
    """Tells each person whose live delegation ends within `remind_days`, once per delegation, through the
    installation's `email` notifier (`task_delivery`); the number of reminders sent. A delegation that is
    revoked, expired or already reminded is left alone, and so is one whose installation has no notifier
    (the console shows its expiry). Runs at the platform scope, as the scheduler pass does."""
    from choregos_core.domain import Message

    from .task_delivery import notifier_for

    now = now or utcnow()
    rows = (
        (
            await session.execute(
                select(Delegation).where(Delegation.revoked_at.is_(None), Delegation.reminded_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    sent = 0
    for delegation in rows:
        installation = await session.get(AssistantInstallation, delegation.installation_id)
        user = await session.get(User, delegation.user_id)
        expires = _utc(delegation.expires_at)
        if installation is None or user is None or expires <= now:
            continue
        if expires - now > timedelta(days=remind_days_of(installation)):
            continue
        notifier = notifier_for(installation, "email")
        if notifier is None:
            continue
        try:
            await notifier.send(
                user.email,
                Message(
                    title=f"Your delegation of {delegation.task} ends soon",
                    body=(
                        f"The standing delegation that lets `{delegation.task}` of `{installation.slug}` "
                        f"read on your behalf ends on {expires:%Y-%m-%d}. Agree to it again to keep the "
                        "task running while you are away."
                    ),
                    context={"task": delegation.task, "delegation": delegation.id},
                ),
            )
        except Exception as failure:  # a failed send is tried again at the next pass
            log.warning("delegation.reminder_failed", delegation=delegation.id, error=str(failure)[:200])
            continue
        delegation.reminded_at = now
        sent += 1
    await session.flush()
    return sent


# -- erasure -----------------------------------------------------------------------------------------------


async def _erase(session: AsyncSession, installation: AssistantInstallation, user_id: str) -> int:
    """`DELETE /me/assistants/{org}/{slug}`: the person's delegations there, revoked then deleted."""
    rows = list(
        (
            await session.execute(
                select(Delegation).where(
                    Delegation.installation_id == installation.id, Delegation.user_id == user_id
                )
            )
        ).scalars()
    )
    for delegation in rows:
        if delegation.revoked_at is None:
            await _revoke(session, delegation, utcnow())
        linked = await session.execute(
            select(TaskSubscription).where(TaskSubscription.delegation_id == delegation.id)
        )
        for sub in linked.scalars():
            sub.delegation_id = None
        await session.delete(delegation)
    await session.flush()
    return len(rows)


USER_ERASERS["delegations"] = _erase
