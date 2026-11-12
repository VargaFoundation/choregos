# SPDX-License-Identifier: Apache-2.0
"""Changing the instance's identity provider: the re-link window and its plan (S70-20, ADR 0160).

An account is bound to its provider's subject (ADR 0100), and a subject only means something at the
issuer that gave it. A new provider gives every person a new subject. Each account is moved to its new
subject at the person's first sign-in through the new provider, while a re-link window is open
(`CHOREGOS_OIDC_RELINK_UNTIL`): the provider must assert the address with `email_verified: true`, and
exactly one account bound to another issuer's subject must carry that address, letter case aside.

The plan says, before and during the window, which accounts that rule will move, which it will refuse
(two accounts with one address), which the provider already holds and which it does not concern. The
sign-in and the plan read the same two functions, `concerned` and `relink_candidates`, so the plan
cannot promise what the sign-in refuses.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import User
from ..db.session import in_platform_scope

#: Subjects the instance provider never gives (ADR 0100): the development sign-in's and a tenant's own
#: provider's, which re-links its members itself (ADR 0108).
RESERVED_PREFIXES = ("dev|", "tenant:")

WindowState = Literal["open", "closed", "none"]


def normalised_issuer(issuer: str) -> str:
    """An issuer as it is recorded: the configured string without its trailing slash."""
    return issuer.rstrip("/")


def concerned(user: User) -> bool:
    """An account the instance's provider binds: a subject that is neither a development one nor a
    tenant's, and a person, not a machine account (ADR 0131)."""
    sub = user.oidc_sub
    return bool(sub) and not str(sub).startswith(RESERVED_PREFIXES) and not user.is_service


def bound_elsewhere(user: User, issuer: str) -> bool:
    """Concerned, and bound to a subject of another issuer than `issuer` (or of an issuer not recorded:
    an account bound before ADR 0160)."""
    return concerned(user) and user.oidc_issuer != normalised_issuer(issuer)


async def relink_candidates(session: AsyncSession, email: str, issuer: str) -> list[User]:
    """The accounts a sign-in at `issuer`, with this verified address, could move to its subject: those
    bound to another issuer's subject whose address is the same, letter case aside. More than one is an
    ambiguity the sign-in refuses."""
    rows = (await session.execute(select(User).where(func.lower(User.email) == email.lower()))).scalars()
    return sorted((user for user in rows if bound_elsewhere(user, issuer)), key=lambda user: user.id)


def window_state(settings: Settings, now: datetime | None = None) -> WindowState:
    if settings.oidc_relink_until is None:
        return "none"
    return "open" if settings.relink_window_open(now) else "closed"


async def relink_plan(
    session: AsyncSession, settings: Settings, issuer: str | None = None, now: datetime | None = None
) -> dict[str, object]:
    """What a re-link window for `issuer` (default: the configured one) does to every account.

    - `pending`: bound to another issuer's subject, alone with its address: moved at its first sign-in
      through `issuer` while the window is open, if the provider asserts the address verified;
    - `ambiguous`: the same, but another such account has the same address, letter case aside: the
      sign-in refuses both until an administrator resolves them;
    - `current`: already bound to a subject of `issuer`;
    - `unbound`: no subject yet, linked by address at the first sign-in, window or not (ADR 0100);
    - `not_concerned`: development subjects, a tenant's provider's members, machine accounts.
    """
    target = normalised_issuer(issuer or settings.oidc_issuer)
    async with in_platform_scope(session):
        users = list((await session.execute(select(User).order_by(User.email))).scalars())
    current = unbound = not_concerned = 0
    elsewhere: list[User] = []
    for user in users:
        if not user.oidc_sub or str(user.oidc_sub).startswith("dev|"):
            if user.is_service:
                not_concerned += 1
            else:
                unbound += 1
        elif not concerned(user):
            not_concerned += 1
        elif user.oidc_issuer == target:
            current += 1
        else:
            elsewhere.append(user)
    by_address: dict[str, list[User]] = defaultdict(list)
    for user in elsewhere:
        by_address[user.email.lower()].append(user)

    def row(user: User) -> dict[str, object]:
        same = [other.email for other in by_address[user.email.lower()] if other.id != user.id]
        return {
            "user_id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "issuer": user.oidc_issuer,
            "last_login_at": user.last_login_at,
            "same_address_as": same,
        }

    pending = [row(u) for u in elsewhere if len(by_address[u.email.lower()]) == 1]
    ambiguous = [row(u) for u in elsewhere if len(by_address[u.email.lower()]) > 1]
    configured = normalised_issuer(settings.oidc_issuer)
    return {
        "issuer": target,
        "configured_issuer": configured,
        "window": {
            "state": window_state(settings, now),
            "until": settings.oidc_relink_until,
            "applies": target == configured,
        },
        "counts": {
            "pending": len(pending),
            "ambiguous": len(ambiguous),
            "current": current,
            "unbound": unbound,
            "not_concerned": not_concerned,
        },
        "pending": pending,
        "ambiguous": ambiguous,
        "generated_at": now or datetime.now(UTC),
    }
