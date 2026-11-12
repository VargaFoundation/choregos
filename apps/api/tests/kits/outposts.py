"""Outposts in the tests: a door that has its keys, an instance that enrols as the door will let it,
and the rows a later story writes (a connector at an outpost, a call), written by hand.

What reads or writes an outpost's own rows names the outpost first, as the product does: on SQLite the
call does nothing, on PostgreSQL the rows do not exist for a session that names none, the platform's
scope included. The sessions opened here are the test's own and say so (`orgs="*"`)."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import pytest
from choregos_contracts import OutpostEnrolmentRequest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from httpx import AsyncClient
from sqlalchemy import select

ORG = "varga"
BASE = f"/api/v1/orgs/{ORG}/outposts"
ORIGIN = "https://outposts.example.com"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _raw(public: Any) -> bytes:
    raw: bytes = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return raw


def signing_key() -> str:
    """The public half of a fresh Ed25519 key, as an instance sends it."""
    return _b64(_raw(Ed25519PrivateKey.generate().public_key()))


def sealing_key() -> str:
    """The public half of a fresh X25519 key, as an instance sends it."""
    return _b64(_raw(X25519PrivateKey.generate().public_key()))


@dataclass(frozen=True)
class Door:
    """What the deployment was given for its door: the key that signs, and one announced beside it."""

    signing_pem: str
    signing_public: str
    announced: str

    @property
    def keys(self) -> list[str]:
        return [self.signing_public, self.announced]


def door_keys() -> Door:
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return Door(signing_pem=pem, signing_public=_b64(_raw(private.public_key())), announced=signing_key())


def configure(monkeypatch: pytest.MonkeyPatch, **settings: str | None) -> None:
    """Set (or, with `None`, unset) `CHOREGOS_<NAME>` and have the next request read it."""
    from choregos_api.config import reset_settings_cache

    for name, value in settings.items():
        variable = f"CHOREGOS_{name.upper()}"
        if value is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, value)
    reset_settings_cache()


def switch_on(monkeypatch: pytest.MonkeyPatch) -> Door:
    """Outposts on, with a door that has an origin, a signing key and a key announced for a rotation."""
    door = door_keys()
    configure(
        monkeypatch,
        outposts="on",
        outposts_door_url=ORIGIN,
        outposts_door_signing_key=door.signing_pem,
        outposts_door_announced_keys=f'["{door.announced}"]',
    )
    return door


async def create(client: AsyncClient, slug: str = "factory-floor", **body: Any) -> dict[str, Any]:
    response = await client.post(BASE, json={"slug": slug, **body})
    assert response.status_code == 201, response.text
    return dict(response.json())


async def token_for(client: AsyncClient, slug: str = "factory-floor", **body: Any) -> str:
    response = await client.post(f"{BASE}/{slug}/enrolments", json=body or None)
    assert response.status_code == 201, response.text
    return str(response.json()["token"])


def enrolment_request(token: str, **changes: Any) -> OutpostEnrolmentRequest:
    body: dict[str, Any] = {
        "protocol": 1,
        "token": token,
        "signing_key": signing_key(),
        "sealing_key": sealing_key(),
        "daemon_version": "0.27.0",
        "capabilities": ["tools"],
        "facts": {"hostname": "outpost-0.tools.svc", "platform": "linux/amd64"},
    }
    return OutpostEnrolmentRequest.model_validate(body | changes)


async def enrol(token: str, **changes: Any) -> Any:
    """Enrol as the door will: a session of its own that has no scope yet, committed when it worked
    and rolled back when it was refused."""
    from choregos_api.db.session import get_sessionmaker
    from choregos_api.services import outposts as service

    async with get_sessionmaker()() as session:
        try:
            enrolled = await service.enrol(session, enrolment_request(token, **changes))
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        return enrolled


async def enrolled_instance(client: AsyncClient, slug: str = "factory-floor", **changes: Any) -> str:
    """A pending instance of the outpost: its id."""
    enrolled = await enrol(await token_for(client, slug), **changes)
    return str(enrolled.instance.id)


async def fingerprint_of_instance(client: AsyncClient, slug: str, instance_id: str) -> str:
    listed = (await client.get(f"{BASE}/{slug}/instances")).json()["items"]
    return str(next(i["fingerprint"] for i in listed if i["id"] == instance_id))


async def approve(client: AsyncClient, slug: str, instance_id: str) -> dict[str, Any]:
    response = await client.post(
        f"{BASE}/{slug}/instances/{instance_id}/approve",
        json={"fingerprint": await fingerprint_of_instance(client, slug, instance_id)},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def outpost_row(slug: str, org: str = ORG) -> Any:
    from choregos_api.db.models import Organization, Outpost
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return (
            await session.execute(
                select(Outpost)
                .join(Organization, Organization.id == Outpost.org_id)
                .where(Organization.slug == org, Outpost.slug == slug)
            )
        ).scalar_one()


async def rows_of(slug: str, model: Any) -> list[Any]:
    """The rows of one of an outpost's own tables, read under its name."""
    from choregos_api.db.session import name_the_outpost, session_scope

    outpost = await outpost_row(slug)
    async with session_scope(orgs="*") as session:
        await name_the_outpost(session, outpost.id)
        return list((await session.execute(select(model).where(model.outpost_id == outpost.id))).scalars())


async def connector_at(slug: str, name: str = "tracker-at-the-outpost") -> str:
    """An `mcp-outpost` connector that routes to the outpost, as S65-13 will declare one: its id."""
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope

    outpost = await outpost_row(slug)
    async with session_scope(orgs="*") as session:
        connector = OrgConnector(
            org_id=outpost.org_id,
            name=name,
            kind="mcp",
            type="mcp-outpost",
            config={"outpost": slug, "endpoint": "tracker", "timeout_s": 30},
            outpost_id=outpost.id,
        )
        session.add(connector)
        await session.flush()
        return str(connector.id)


async def audit_rows(action: str) -> list[Any]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return list(
            (
                await session.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.ts))
            ).scalars()
        )
