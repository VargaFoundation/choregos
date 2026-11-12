"""Outposts, as their organisation's administrator manages them (S65-11, ADRs 0147 and 0148).

An outpost is a place a person enrols, which only dials out. These tests cover the platform's records
and the management routes: the switch, the enrolment token (single use, 15 minutes at most, kept as a
digest), the instance born pending, the approval against a fingerprint in a person's fresh session,
the revocation that moves the epoch, pausing, resuming and deleting, and the calls shown as digests.
The door an outpost dials is not here: an instance enrols through the service the door will call.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import choregos_contracts as contracts
import pytest
from choregos_contracts import OutpostDoorCode, OutpostEnrolmentAnswer, fingerprint_of
from httpx import ASGITransport, AsyncClient
from jsonschema import Draft202012Validator
from sqlalchemy import select

from .kits import outposts as kit
from .kits.outposts import BASE, ORIGIN

SLUG = "factory-floor"
ONE = f"{BASE}/{SLUG}"


def _valid(name: str, body: Any) -> None:
    """The body is what the contract's schema of that name describes."""
    spec = contracts.load_openapi()
    schema = {"$ref": f"#/components/schemas/{name}", "components": spec["components"]}
    errors = [error.message for error in Draft202012Validator(schema).iter_errors(body)]
    assert not errors, f"not a contract `{name}`: {errors}"


def _refused(response: Any, code: str, status: int = 409) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = dict(response.json())
    assert body["code"] == code, body
    _valid("OutpostConflict", body)
    return body


@pytest.fixture
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    """A deployment whose operator turned outposts on and gave the door its origin and its keys."""
    return kit.switch_on(monkeypatch)


# ───────────────────────── the switch, and an outpost's record ─────────────────────────


async def test_with_outposts_off_a_creation_is_refused(client: AsyncClient, admin: str) -> None:
    """`CHOREGOS_OUTPOSTS` is off unless turned on (ADR 0147 §12)."""
    from choregos_api.config import Settings

    assert Settings.model_fields["outposts"].default is False
    refused = _refused(await client.post(BASE, json={"slug": SLUG}), "outposts_disabled")
    assert "CHOREGOS_OUTPOSTS" in refused["detail"]
    assert (await client.get(BASE)).json() == {"items": []}, "nothing was written"


async def test_an_administrator_creates_an_outpost_and_reads_it_back(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    created = await client.post(BASE, json={"slug": SLUG, "display_name": "The factory floor"})
    assert created.status_code == 201, created.text
    outpost = created.json()
    _valid("Outpost", outpost)
    assert outpost | {"created_at": None} == {
        "slug": SLUG,
        "display_name": "The factory floor",
        "status": "active",
        "capabilities": ["tools"],
        "epoch": 0,
        "online": False,
        "last_seen_at": None,
        "instances": {"pending": 0, "active": 0, "revoked": 0},
        "endpoints": [],
        "connectors": [],
        "door_keys": door.keys,
        "created_by": admin,
        "created_at": None,
    }
    assert (await client.get(ONE)).json() == outpost
    listing = (await client.get(BASE)).json()
    _valid("OutpostList", listing)
    assert listing["items"] == [outpost]
    (entry,) = await kit.audit_rows("outpost.create")
    row = await kit.outpost_row(SLUG)
    assert (entry.actor_kind, entry.target_type, entry.target_id) == ("user", "outpost", row.id)
    assert entry.payload == {"outpost": SLUG, "capabilities": ["tools"]}


async def test_an_outposts_name_is_its_slug_when_none_is_given_and_a_slug_is_taken_once(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    assert (await kit.create(client))["display_name"] == SLUG
    refused = _refused(await client.post(BASE, json={"slug": SLUG}), "outpost_exists")
    assert refused["outpost"] == SLUG
    assert [o["slug"] for o in (await client.get(BASE)).json()["items"]] == [SLUG]


@pytest.mark.parametrize(
    "body",
    [
        {"slug": "Factory"},
        {"slug": "-floor"},
        {"slug": SLUG, "capabilities": ["runs"]},
        {"slug": SLUG, "capabilities": []},
        {"slug": SLUG, "capabilities": ["tools", "tools"]},
        {"slug": SLUG, "display_name": "x" * 201},
        {"slug": SLUG, "url": "https://tools.example.com"},
    ],
    ids=["capital", "hyphen-first", "runs", "no-capability", "twice", "long-name", "an-address"],
)
async def test_a_creation_the_contract_does_not_describe_is_a_422(
    client: AsyncClient, admin: str, door: kit.Door, body: dict[str, Any]
) -> None:
    """`runs` is reserved (ADR 0149 §12), and nothing of an outpost's side is stored (ADR 0147 §7)."""
    assert (await client.post(BASE, json=body)).status_code == 422
    assert (await client.get(BASE)).json() == {"items": []}


async def test_an_unknown_outpost_is_a_404_on_every_route_that_names_one(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    await kit.create(client)
    instance = "0" * 64
    for method, path, body in (
        ("GET", "", None),
        ("DELETE", "", None),
        ("POST", "/pause", None),
        ("POST", "/resume", None),
        ("POST", "/revoke", None),
        ("POST", "/enrolments", None),
        ("GET", "/instances", None),
        ("POST", f"/instances/{instance}/approve", {"fingerprint": "SHA256:" + "a" * 43}),
        ("POST", f"/instances/{instance}/revoke", None),
        ("GET", "/calls", None),
    ):
        response = await client.request(method, f"{BASE}/nowhere{path}", json=body)
        assert response.status_code == 404, f"{method} {path}: {response.status_code} {response.text}"
    for path in (f"/instances/{instance}/approve", f"/instances/{instance}/revoke"):
        body = {"fingerprint": "SHA256:" + "a" * 43} if path.endswith("approve") else None
        assert (await client.post(f"{ONE}{path}", json=body)).status_code == 404, "no such instance"


# ───────────────────────── an enrolment token ─────────────────────────


async def test_an_enrolment_token_is_shown_once_with_what_the_operator_pins(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    """Single use, 15 minutes at most, stored as a digest (ADR 0147 §4); it comes with the door's
    origin and the door's keys, which the outpost's policy file pins (ADR 0148 §13)."""
    from choregos_api.db.models import OutpostEnrolment

    await kit.create(client)
    before = datetime.now(UTC)
    issued = await client.post(f"{ONE}/enrolments")
    assert issued.status_code == 201, issued.text
    created = issued.json()
    _valid("OutpostEnrolmentCreated", created)
    token = created["token"]
    assert kit.enrolment_request(token).token == token, "the door's own request model takes this token"
    assert created["control_plane"] == ORIGIN
    assert created["door_keys"] == door.keys, "the key that signs first, then the announced one"
    expires = datetime.fromisoformat(created["expires_at"])
    assert timedelta(minutes=14, seconds=50) < expires - before <= timedelta(minutes=15, seconds=5)

    (row,) = await kit.rows_of(SLUG, OutpostEnrolment)
    assert row.id == created["id"] and row.used_at is None and row.created_by == admin
    assert row.token_digest == hashlib.sha256(token.encode()).hexdigest()
    kept = {column: getattr(row, column) for column in OutpostEnrolment.__table__.columns.keys()}  # noqa: SIM118
    assert token not in map(str, kept.values()), "the token itself is stored nowhere"
    (entry,) = await kit.audit_rows("outpost.enrol")
    assert (entry.target_type, entry.target_id) == ("outpost_enrolment", created["id"])
    assert token not in str(entry.payload), "the audit trail names the token, never shows it"
    listing = (await client.get(ONE)).text + (await client.get(f"{ONE}/instances")).text
    assert token not in listing, "no later answer shows it again"


async def test_a_token_lives_fifteen_minutes_at_most(client: AsyncClient, admin: str, door: kit.Door) -> None:
    await kit.create(client)
    assert (await client.post(f"{ONE}/enrolments", json={"expires_in_minutes": 16})).status_code == 422
    assert (await client.post(f"{ONE}/enrolments", json={"expires_in_minutes": 0})).status_code == 422
    short = (await client.post(f"{ONE}/enrolments", json={"expires_in_minutes": 5})).json()
    lifetime = datetime.fromisoformat(short["expires_at"]) - datetime.now(UTC)
    assert timedelta(minutes=4, seconds=50) < lifetime <= timedelta(minutes=5)
    assert contracts.ENROLMENT_TTL_S == 900


async def test_without_a_door_signing_key_no_enrolment_token_is_issued(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An outpost makes only calls signed by a key its policy file pins (ADR 0148 §13): with no key to
    pin, an instance enrolled now could never be handed anything."""
    from choregos_api.db.models import OutpostEnrolment

    kit.configure(monkeypatch, outposts="on", outposts_door_url=ORIGIN)
    outpost = await kit.create(client)
    assert outpost["door_keys"] == [], "empty while the deployment's door has no key"
    refused = _refused(await client.post(f"{ONE}/enrolments"), "door_key_missing")
    assert "CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY" in refused["detail"]
    assert await kit.rows_of(SLUG, OutpostEnrolment) == []
    assert await kit.audit_rows("outpost.enrol") == []


async def test_switched_off_no_token_is_issued_and_what_exists_is_still_managed(
    client: AsyncClient, admin: str, door: kit.Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The switch stops every outpost at once (ADR 0147 §12); what exists can still be read, revoked
    and removed."""
    await kit.create(client)
    kit.configure(monkeypatch, outposts="off")
    _refused(await client.post(f"{ONE}/enrolments"), "outposts_disabled")
    _refused(await client.post(BASE, json={"slug": "another"}), "outposts_disabled")
    assert (await client.get(ONE)).status_code == 200
    assert [o["slug"] for o in (await client.get(BASE)).json()["items"]] == [SLUG]
    assert (await client.post(f"{ONE}/revoke")).json()["status"] == "revoked"
    assert (await client.delete(ONE)).status_code == 204


# ───────────────────────── an instance enrols: born pending ─────────────────────────


async def test_an_instance_is_born_pending(client: AsyncClient, admin: str, door: kit.Door) -> None:
    from choregos_api.db.models import OutpostEnrolment
    from choregos_api.services.outposts import outpost_id_of_instance

    await kit.create(client)
    token = await kit.token_for(client)
    enrolled = await kit.enrol(token, facts={"hostname": "outpost-0"})
    instance = enrolled.instance
    assert (instance.status, enrolled.again, enrolled.organisation) == ("pending", False, kit.ORG)
    assert instance.fingerprint == fingerprint_of(instance.signing_key)
    answer = OutpostEnrolmentAnswer(  # what the door answers is the contract's, with this id
        instance_id=instance.id,
        organisation=enrolled.organisation,
        outpost=enrolled.outpost.slug,
        status="pending",
        fingerprint=instance.fingerprint,
        protocol={"current": 1, "minimum": 1},  # type: ignore[arg-type]
        server_time=datetime.now(UTC),
    )
    assert answer.outpost == SLUG
    row = await kit.outpost_row(SLUG)
    assert outpost_id_of_instance(instance.id) == row.id, "an instance's id says which outpost to name"

    listed = (await client.get(f"{ONE}/instances")).json()
    _valid("OutpostInstanceList", listed)
    (shown,) = listed["items"]
    assert shown | {"enrolled_at": None} == {
        "id": instance.id,
        "status": "pending",
        "fingerprint": instance.fingerprint,
        "daemon_version": "0.27.0",
        "protocol": 1,
        "capabilities": ["tools"],
        "endpoints": [],
        "facts": {"hostname": "outpost-0"},
        "enrolled_at": None,
        "approved_by": None,
        "approved_at": None,
        "revoked_by": None,
        "revoked_at": None,
        "last_seen_at": None,
        "online": False,
        "in_flight": 0,
    }
    assert instance.signing_key not in str(listed) and instance.sealing_key not in str(listed)
    outpost = (await client.get(ONE)).json()
    assert outpost["instances"] == {"pending": 1, "active": 0, "revoked": 0}
    assert outpost["online"] is False and outpost["epoch"] == 0, "a pending instance is handed nothing"

    (used,) = await kit.rows_of(SLUG, OutpostEnrolment)
    assert used.used_at is not None and used.instance_id == instance.id
    by_instance = [e for e in await kit.audit_rows("outpost.enrol") if e.target_type == "outpost_instance"]
    (entry,) = by_instance
    assert (entry.actor_kind, entry.target_id) == ("outpost", instance.id)
    assert entry.payload == {
        "outpost": SLUG,
        "enrolment": used.id,
        "fingerprint": instance.fingerprint,
        "again": False,
    }


async def test_an_enrolment_token_used_twice_is_refused(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    from choregos_api.services.outposts import EnrolmentRefused

    await kit.create(client)
    token = await kit.token_for(client)
    first = await kit.enrol(token)
    with pytest.raises(EnrolmentRefused) as refused:
        await kit.enrol(token)
    assert refused.value.code is OutpostDoorCode.INVALID_ENROLMENT_TOKEN
    assert [i["id"] for i in (await client.get(f"{ONE}/instances")).json()["items"]] == [first.instance.id]


async def test_an_enrolment_token_is_refused_after_fifteen_minutes(
    client: AsyncClient, admin: str, door: kit.Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.services import outposts as service
    from choregos_api.services.outposts import EnrolmentRefused

    await kit.create(client)
    late, in_time = await kit.token_for(client), await kit.token_for(client)
    now = datetime.now(UTC)
    monkeypatch.setattr(service, "utcnow", lambda: now + timedelta(minutes=15, seconds=1))
    with pytest.raises(EnrolmentRefused) as refused:
        await kit.enrol(late)
    assert refused.value.code is OutpostDoorCode.INVALID_ENROLMENT_TOKEN
    monkeypatch.setattr(service, "utcnow", lambda: now + timedelta(minutes=14, seconds=30))
    assert (await kit.enrol(in_time)).instance.status == "pending"
    monkeypatch.undo()
    assert len((await client.get(f"{ONE}/instances")).json()["items"]) == 1


async def test_a_token_enrols_at_its_own_outpost_and_nowhere_else(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    """The token is bound to one outpost: the digest is looked for among that outpost's tokens."""
    from choregos_api.services.outposts import ENROLMENT_TOKEN_PREFIX, EnrolmentRefused

    await kit.create(client)
    await kit.create(client, "cold-store")
    token = await kit.token_for(client)
    elsewhere = (await kit.outpost_row("cold-store")).id.replace("-", "")
    moved = ENROLMENT_TOKEN_PREFIX + elsewhere + token[len(ENROLMENT_TOKEN_PREFIX) + 32 :]
    for forged in (moved, "x" + token[1:], token[:-1] + ("A" if token[-1] != "A" else "B"), "a" * 40):
        with pytest.raises(EnrolmentRefused) as refused:
            await kit.enrol(forged)
        assert refused.value.code is OutpostDoorCode.INVALID_ENROLMENT_TOKEN, forged
    assert (await client.get(f"{BASE}/cold-store/instances")).json() == {"items": []}
    assert (await kit.enrol(token)).outpost.slug == SLUG, "a refused enrolment did not use the token up"


async def test_a_pending_key_enrols_again_as_the_same_instance_under_a_new_token(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    """A daemon whose first answer was lost on the way keeps its keys (ADR 0148 §3)."""
    await kit.create(client)
    key = kit.signing_key()
    first = await kit.enrol(await kit.token_for(client), signing_key=key)
    resealed = kit.sealing_key()
    second = await kit.enrol(
        await kit.token_for(client), signing_key=key, sealing_key=resealed, daemon_version="0.27.1"
    )
    assert second.again is True and second.instance.id == first.instance.id
    (shown,) = (await client.get(f"{ONE}/instances")).json()["items"]
    assert (shown["id"], shown["status"], shown["daemon_version"]) == (first.instance.id, "pending", "0.27.1")
    from choregos_api.db.models import OutpostInstance

    (row,) = await kit.rows_of(SLUG, OutpostInstance)
    assert row.sealing_key == resealed, "the sealing key the last signed enrolment carried"


async def test_a_key_ever_approved_or_revoked_does_not_enrol_again(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    """A revoked key does not come back; and a refused enrolment does not use the token up."""
    from choregos_api.services.outposts import EnrolmentRefused

    await kit.create(client)
    key = kit.signing_key()
    instance = await kit.enrolled_instance(client, signing_key=key)
    await kit.approve(client, SLUG, instance)
    token = await kit.token_for(client)
    with pytest.raises(EnrolmentRefused) as approved:
        await kit.enrol(token, signing_key=key)
    assert approved.value.code is OutpostDoorCode.KEY_ALREADY_ENROLLED
    assert (await client.post(f"{ONE}/instances/{instance}/revoke")).status_code == 200
    with pytest.raises(EnrolmentRefused) as revoked:
        await kit.enrol(token, signing_key=key)
    assert revoked.value.code is OutpostDoorCode.KEY_ALREADY_ENROLLED
    other = await kit.enrol(token)
    assert other.instance.id != instance, "the token was still whole: another key enrols with it"
    statuses = sorted(i["status"] for i in (await client.get(f"{ONE}/instances")).json()["items"])
    assert statuses == ["pending", "revoked"]


async def test_a_key_pending_at_another_outpost_is_refused(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    from choregos_api.services.outposts import EnrolmentRefused

    await kit.create(client)
    await kit.create(client, "cold-store")
    key = kit.signing_key()
    await kit.enrolled_instance(client, signing_key=key)
    token = await kit.token_for(client, "cold-store")
    with pytest.raises(EnrolmentRefused) as refused:
        await kit.enrol(token, signing_key=key)
    assert refused.value.code is OutpostDoorCode.KEY_ALREADY_ENROLLED
    assert (await client.get(f"{BASE}/cold-store/instances")).json() == {"items": []}
    assert (await kit.enrol(token)).outpost.slug == "cold-store", "the token was not used up"


# ───────────────────────── a person approves, against the fingerprint ─────────────────────────


async def test_a_person_approves_a_pending_instance_against_its_fingerprint(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    await kit.create(client)
    instance = await kit.enrolled_instance(client)
    fingerprint = await kit.fingerprint_of_instance(client, SLUG, instance)
    approve = f"{ONE}/instances/{instance}/approve"

    another = fingerprint_of(kit.signing_key())
    _refused(await client.post(approve, json={"fingerprint": another}), "fingerprint_mismatch")
    assert (await client.post(approve, json={"fingerprint": "09wT63"})).status_code == 422
    assert (await client.post(approve, json={})).status_code == 422
    (still,) = (await client.get(f"{ONE}/instances")).json()["items"]
    assert still["status"] == "pending" and await kit.audit_rows("outpost.approve") == []

    approved = await client.post(approve, json={"fingerprint": fingerprint})
    assert approved.status_code == 200, approved.text
    shown = approved.json()
    _valid("OutpostInstance", shown)
    assert (shown["status"], shown["approved_by"]) == ("active", admin) and shown["approved_at"]
    assert (await client.get(ONE)).json()["instances"] == {"pending": 0, "active": 1, "revoked": 0}
    _refused(await client.post(approve, json={"fingerprint": fingerprint}), "instance_not_pending")
    (entry,) = await kit.audit_rows("outpost.approve")
    assert (entry.target_type, entry.target_id) == ("outpost_instance", instance)
    assert entry.payload["fingerprint"] == fingerprint and entry.payload["outpost"] == SLUG
    assert 0 <= entry.payload["auth_age_seconds"] < 60


async def test_the_instances_are_listed_pending_ones_first(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    """They are what waits for a person."""
    await kit.create(client)
    active = await kit.enrolled_instance(client)
    await kit.approve(client, SLUG, active)
    revoked = await kit.enrolled_instance(client)
    assert (await client.post(f"{ONE}/instances/{revoked}/revoke")).status_code == 200
    pending = await kit.enrolled_instance(client)
    listed = (await client.get(f"{ONE}/instances")).json()["items"]
    assert [(i["id"], i["status"]) for i in listed] == [
        (pending, "pending"),
        (active, "active"),
        (revoked, "revoked"),
    ]


async def test_a_token_cannot_approve(client: AsyncClient, app: Any, admin: str, door: kit.Door) -> None:
    """Enrolment needs a person twice (ADR 0147 §4): an API token of the administrator is not one."""
    await kit.create(client)
    instance = await kit.enrolled_instance(client)
    fingerprint = await kit.fingerprint_of_instance(client, SLUG, instance)
    token = (await client.post("/api/v1/me/tokens", json={"name": "ci", "scopes": ["*"]})).json()["token"]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    ) as machine:
        assert (await machine.get(f"{ONE}/instances")).status_code == 200, "the token does manage outposts"
        refused = await machine.post(f"{ONE}/instances/{instance}/approve", json={"fingerprint": fingerprint})
        assert refused.status_code == 403 and "person's session" in refused.text, refused.text
    (still,) = (await client.get(f"{ONE}/instances")).json()["items"]
    assert still["status"] == "pending" and await kit.audit_rows("outpost.approve") == []


async def test_an_approval_needs_a_recent_authentication(
    client: AsyncClient, admin: str, door: kit.Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The condition `decide` puts on a decision: a session left open does not approve for its owner."""
    from choregos_api import freshness

    await kit.create(client)
    instance = await kit.enrolled_instance(client)
    fingerprint = await kit.fingerprint_of_instance(client, SLUG, instance)
    later = time.time() + 11 * 60
    monkeypatch.setattr(freshness.time, "time", lambda: later)
    old = await client.post(f"{ONE}/instances/{instance}/approve", json={"fingerprint": fingerprint})
    assert old.status_code == 401 and old.json()["errors"][0]["error"] == "step_up_required", old.text
    monkeypatch.undo()
    (still,) = (await client.get(f"{ONE}/instances")).json()["items"]
    assert still["status"] == "pending"
    assert (await kit.approve(client, SLUG, instance))["status"] == "active"


# ───────────────────────── revoking moves the epoch ─────────────────────────


async def test_revoking_an_instance_moves_the_epoch(client: AsyncClient, admin: str, door: kit.Door) -> None:
    """Nothing in flight at the outpost is taken under the former epoch (ADR 0147 §5)."""
    await kit.create(client)
    kept = await kit.enrolled_instance(client)
    instance = await kit.enrolled_instance(client)
    await kit.approve(client, SLUG, instance)
    assert (await client.get(ONE)).json()["epoch"] == 0, "enrolling and approving move nothing"

    revoked = await client.post(f"{ONE}/instances/{instance}/revoke")
    assert revoked.status_code == 200, revoked.text
    shown = revoked.json()
    _valid("OutpostInstance", shown)
    assert (shown["status"], shown["revoked_by"]) == ("revoked", admin) and shown["revoked_at"]
    outpost = (await client.get(ONE)).json()
    assert outpost["epoch"] == 1 and outpost["status"] == "active"
    assert outpost["instances"] == {"pending": 1, "active": 0, "revoked": 1}

    again = await client.post(f"{ONE}/instances/{instance}/revoke")
    assert again.status_code == 200 and again.json()["revoked_at"] == shown["revoked_at"]
    assert (await client.get(ONE)).json()["epoch"] == 1, "revoking a revoked instance changes nothing"
    (entry,) = await kit.audit_rows("outpost.revoke")
    assert (entry.target_type, entry.target_id, entry.payload["epoch"]) == ("outpost_instance", instance, 1)

    assert (await client.post(f"{ONE}/instances/{kept}/revoke")).json()["status"] == "revoked"
    assert (await client.get(ONE)).json()["epoch"] == 2, "a pending instance's revocation moves it too"
    fingerprint = await kit.fingerprint_of_instance(client, SLUG, kept)
    _refused(
        await client.post(f"{ONE}/instances/{kept}/approve", json={"fingerprint": fingerprint}),
        "instance_not_pending",
    )


async def test_revoking_the_outpost_revokes_every_instance_and_is_final(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    from choregos_api.services.outposts import EnrolmentRefused

    await kit.create(client)
    pending = await kit.enrolled_instance(client)
    active = await kit.enrolled_instance(client)
    await kit.approve(client, SLUG, active)
    outstanding = await kit.token_for(client)

    revoked = await client.post(f"{ONE}/revoke")
    assert revoked.status_code == 200, revoked.text
    outpost = revoked.json()
    _valid("Outpost", outpost)
    assert (outpost["status"], outpost["epoch"]) == ("revoked", 1)
    assert outpost["instances"] == {"pending": 0, "active": 0, "revoked": 2}
    listed = (await client.get(f"{ONE}/instances")).json()["items"]
    assert {i["id"]: (i["status"], i["revoked_by"]) for i in listed} == {
        pending: ("revoked", admin),
        active: ("revoked", admin),
    }

    assert (await client.post(f"{ONE}/revoke")).json()["epoch"] == 1, "revoking again changes nothing"
    (entry,) = [e for e in await kit.audit_rows("outpost.revoke") if e.target_type == "outpost"]
    assert entry.payload == {"outpost": SLUG, "epoch": 1}
    for gesture in ("pause", "resume", "enrolments"):
        _refused(await client.post(f"{ONE}/{gesture}"), "outpost_revoked")
    with pytest.raises(EnrolmentRefused) as refused:
        await kit.enrol(outstanding)
    assert refused.value.code is OutpostDoorCode.INVALID_ENROLMENT_TOKEN, (
        "a token issued before does not enrol"
    )
    fingerprint = await kit.fingerprint_of_instance(client, SLUG, pending)
    _refused(
        await client.post(f"{ONE}/instances/{pending}/approve", json={"fingerprint": fingerprint}),
        "outpost_revoked",
    )


# ───────────────────────── pausing, resuming, deleting ─────────────────────────


async def test_a_paused_outpost_is_resumed(client: AsyncClient, admin: str, door: kit.Door) -> None:
    """Pausing is how a healthy outpost is held: it moves no epoch (ADR 0147, consequences)."""
    await kit.create(client)
    assert (await client.post(f"{ONE}/resume")).json()["status"] == "active", "nothing to resume"
    assert await kit.audit_rows("outpost.resume") == []

    paused = await client.post(f"{ONE}/pause")
    assert paused.status_code == 200, paused.text
    _valid("Outpost", paused.json())
    assert (paused.json()["status"], paused.json()["epoch"]) == ("paused", 0)
    assert (await client.post(f"{ONE}/pause")).json()["status"] == "paused"
    assert len(await kit.audit_rows("outpost.pause")) == 1, "pausing a paused outpost changes nothing"
    assert (await kit.enrol(await kit.token_for(client))).instance.status == "pending"

    resumed = await client.post(f"{ONE}/resume")
    assert resumed.status_code == 200 and resumed.json()["status"] == "active", resumed.text
    assert (await client.get(ONE)).json()["epoch"] == 0
    (entry,) = await kit.audit_rows("outpost.resume")
    assert (entry.target_type, entry.payload) == ("outpost", {"outpost": SLUG})


async def test_a_revoked_outpost_is_deleted_and_one_that_still_has_connectors_is_not(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    from choregos_api.db.models import OrgConnector, OutpostCall, OutpostEnrolment, OutpostInstance
    from choregos_api.db.session import name_the_outpost, session_scope

    await kit.create(client)
    instance = await kit.enrolled_instance(client)
    await _call(SLUG, "call-left", state="claimed", instance_id=instance, epoch=0)
    row = await kit.outpost_row(SLUG)
    refused = _refused(await client.delete(ONE), "outpost_not_revoked")
    assert refused["outpost"] == SLUG

    connector = await kit.connector_at(SLUG)
    assert (await client.get(ONE)).json()["connectors"] == ["tracker-at-the-outpost"]
    in_use = _refused(await client.delete(ONE), "outpost_in_use")
    assert in_use["connectors"] == ["tracker-at-the-outpost"], "said before `outpost_not_revoked`"
    assert (await client.post(f"{ONE}/revoke")).status_code == 200
    _refused(await client.delete(ONE), "outpost_in_use")
    assert (await client.get(ONE)).status_code == 200 and await kit.audit_rows("outpost.delete") == []

    async with session_scope() as session:
        await session.delete(await session.get(OrgConnector, connector))
    assert (await client.delete(ONE)).status_code == 204
    assert (await client.get(ONE)).status_code == 404
    assert (await client.get(BASE)).json() == {"items": []}
    (entry,) = await kit.audit_rows("outpost.delete")
    assert (entry.target_id, entry.payload) == (row.id, {"outpost": SLUG, "epoch": 1})

    async with session_scope(orgs="*") as session:
        await name_the_outpost(session, row.id)  # the outpost that is gone: its rows went with it
        for model in (OutpostInstance, OutpostEnrolment, OutpostCall):
            left = (await session.execute(select(model).where(model.outpost_id == row.id))).scalars().all()
            assert left == [], f"{model.__tablename__} outlived its outpost"
    again = await kit.create(client)
    assert (again["epoch"], again["instances"]["pending"]) == (0, 0), "the slug is free, and starts anew"


# ───────────────────────── presence, and the calls as digests ─────────────────────────


async def _seen(instance_id: str, ago_s: float, endpoints: list[str]) -> None:
    """What the door writes at a poll: when, and the aliases the instance's policy file serves."""
    from choregos_api.db.models import OutpostInstance
    from choregos_api.db.session import name_the_outpost, session_scope
    from choregos_api.services.outposts import outpost_id_of_instance

    async with session_scope() as session:
        await name_the_outpost(session, outpost_id_of_instance(instance_id))
        instance = await session.get(OutpostInstance, instance_id)
        assert instance is not None
        instance.last_seen_at = datetime.now(UTC) - timedelta(seconds=ago_s)
        instance.endpoints = endpoints
        await session.flush()


async def test_an_outpost_is_online_while_an_active_instance_polled_within_the_window(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    from choregos_api.services.outposts import POLL_WINDOW_S

    await kit.create(client)
    pending = await kit.enrolled_instance(client)
    active = await kit.enrolled_instance(client)
    await kit.approve(client, SLUG, active)
    await _seen(pending, 1, ["never-approved"])
    outpost = (await client.get(ONE)).json()
    assert (outpost["online"], outpost["endpoints"]) == (False, []), "a pending instance does not count"

    await _seen(active, POLL_WINDOW_S - 5, ["tracker", "forge"])
    outpost = (await client.get(ONE)).json()
    assert (outpost["online"], outpost["endpoints"]) == (True, ["forge", "tracker"])
    assert outpost["last_seen_at"] is not None
    shown = {i["id"]: i["online"] for i in (await client.get(f"{ONE}/instances")).json()["items"]}
    assert shown == {pending: False, active: True}

    await _seen(active, POLL_WINDOW_S + 5, ["tracker"])
    outpost = (await client.get(ONE)).json()
    assert (outpost["online"], outpost["endpoints"]) == (False, ["tracker"]), "names stay, presence goes"


async def _call(slug: str, call_id: str, **columns: Any) -> None:
    """A row of the rendezvous, as S65-13 will write one and S65-12 claim it."""
    from choregos_api.db.models import OutpostCall
    from choregos_api.db.session import name_the_outpost, session_scope

    outpost = await kit.outpost_row(slug)
    values: dict[str, Any] = {
        "kind": "call_tool",
        "endpoint": "tracker",
        "tool": "search_issues",
        "write": False,
    }
    async with session_scope() as session:
        await name_the_outpost(session, outpost.id)
        session.add(
            OutpostCall(id=call_id, org_id=outpost.org_id, outpost_id=outpost.id, **(values | columns))
        )
        await session.flush()


async def test_the_recent_calls_are_listed_newest_first_as_digests(
    client: AsyncClient, project: dict[str, Any], door: kit.Door
) -> None:
    """References and digests, never arguments or a result (ADR 0148 §11)."""
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope

    await kit.create(client)
    assert (await client.get(f"{ONE}/calls")).json() == {
        "items": [],
        "meta": {"next_cursor": None, "has_more": False},
    }
    instance = await kit.enrolled_instance(client)
    await kit.approve(client, SLUG, instance)
    connector = await kit.connector_at(SLUG)
    start = datetime.now(UTC) - timedelta(minutes=5)
    digest = "ab" * 32
    await _call(SLUG, "call-1", created_at=start, state="answered", outcome="result", is_error=False,
                instance_id=instance, epoch=0, connector_id=connector, arguments_sha256=digest,
                result_sha256="cd" * 32, claimed_at=start, finished_at=start)  # fmt: skip
    await _call(SLUG, "call-2", created_at=start + timedelta(minutes=1), state="refused",
                refusal="write_denied", tool="create_issue", write=True, instance_id=instance, epoch=0,
                action_id="0192f0c0-0000-7000-8000-000000000001", run_id="DESK-1-t-fix-1",
                project_id=project["id"])  # fmt: skip
    await _call(SLUG, "call-3", created_at=start + timedelta(minutes=2), state="claimed",
                instance_id=instance, epoch=0, claimed_at=start, connector_id=connector)  # fmt: skip
    await _call(SLUG, "call-4", created_at=start + timedelta(minutes=3), kind="list_tools", tool=None)

    page = (await client.get(f"{ONE}/calls")).json()
    _valid("OutpostCallPage", page)
    assert [c["id"] for c in page["items"]] == ["call-4", "call-3", "call-2", "call-1"]
    oldest = page["items"][-1]
    assert oldest | {"created_at": None, "claimed_at": None, "finished_at": None} == {
        "id": "call-1",
        "kind": "call_tool",
        "state": "answered",
        "connector": "tracker-at-the-outpost",
        "endpoint": "tracker",
        "tool": "search_issues",
        "write": False,
        "instance_id": instance,
        "epoch": 0,
        "outcome": "result",
        "is_error": False,
        "refusal": None,
        "arguments_sha256": digest,
        "result_sha256": "cd" * 32,
        "project": None,
        "run_id": None,
        "action_id": None,
        "created_at": None,
        "claimed_at": None,
        "finished_at": None,
    }
    assert set(oldest) == set(
        contracts.load_openapi()["components"]["schemas"]["OutpostCallRecord"]["properties"]
    )
    assert (page["items"][0]["kind"], page["items"][0]["tool"]) == ("list_tools", None)
    refusal = page["items"][2]
    assert (refusal["refusal"], refusal["write"], refusal["outcome"]) == ("write_denied", True, None)
    assert (refusal["project"], refusal["run_id"]) == ("billing-api", "DESK-1-t-fix-1")
    assert refusal["action_id"] == "0192f0c0-0000-7000-8000-000000000001"

    refused = (await client.get(f"{ONE}/calls", params={"state": "refused"})).json()
    assert [c["id"] for c in refused["items"]] == ["call-2"]
    assert (await client.get(f"{ONE}/calls", params={"state": "lost"})).status_code == 422
    first = (await client.get(f"{ONE}/calls", params={"limit": 3})).json()
    assert [c["id"] for c in first["items"]] == ["call-4", "call-3", "call-2"]
    assert first["meta"] == {"next_cursor": "call-2", "has_more": True}
    rest = (await client.get(f"{ONE}/calls", params={"limit": 3, "cursor": "call-2"})).json()
    assert [c["id"] for c in rest["items"]] == ["call-1"] and rest["meta"]["has_more"] is False

    held = {i["id"]: i["in_flight"] for i in (await client.get(f"{ONE}/instances")).json()["items"]}
    assert held == {instance: 1}, "the one call it claimed and has not answered"
    async with session_scope() as session:
        await session.delete(await session.get(OrgConnector, connector))
    gone = (await client.get(f"{ONE}/calls")).json()["items"]
    assert {c["connector"] for c in gone} == {None}, "null once that connector is gone"


async def test_what_the_door_recorded_for_a_call_stays_on_its_row(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    """A daemon sends its answer or its refusal again until the call's deadline, and again after a
    restart: what was recorded, from which instance and under which epoch, is kept on the call's own
    row, apart from its state, so that a later copy reads it back whatever the caller did since."""
    from choregos_api.db.models import OutpostCall
    from choregos_api.db.session import name_the_outpost, session_scope
    from choregos_api.services import outposts as service

    await kit.create(client)
    instance = await kit.enrolled_instance(client)
    await kit.approve(client, SLUG, instance)
    row = await kit.outpost_row(SLUG)
    claimed = datetime.now(UTC) - timedelta(seconds=30)
    await _call(SLUG, "call-1", state="claimed", tool="create_issue", write=True, instance_id=instance,
                epoch=0, claimed_at=claimed, deadline=claimed + timedelta(seconds=300))  # fmt: skip

    async with session_scope(orgs="*") as session:
        await name_the_outpost(session, row.id)
        call = await session.get(OutpostCall, "call-1")
        assert call is not None and (call.recorded, call.recorded_by, call.recorded_epoch) == (
            None,
            None,
            None,
        )
        service.seal_call_arguments(call, {"title": "Door stuck"}, _keyring())
        # What the door will write, once: an answer was taken from this instance under this epoch.
        call.recorded, call.recorded_by, call.recorded_epoch = "answer", instance, 0
        call.recorded_at = datetime.now(UTC)
        call.state = "answered"
        await session.flush()
    async with session_scope(orgs="*") as session:
        await name_the_outpost(session, row.id)
        call = await session.get(OutpostCall, "call-1")
        assert call is not None
        # What its caller does next: the answer said `unanswered`, the write is in doubt, the arguments go.
        call.state, call.outcome = "in_doubt", "unanswered"
        call.finished_at = datetime.now(UTC)
        service.clear_call_arguments(call)
        await session.flush()

    (kept,) = await kit.rows_of(SLUG, OutpostCall)
    assert (kept.recorded, kept.recorded_by, kept.recorded_epoch) == ("answer", instance, 0)
    assert kept.recorded_at is not None, "the record outlives the state the call moved to"
    assert (kept.state, kept.instance_id, kept.epoch) == ("in_doubt", instance, 0), "and so does the claim"
    assert kept.arguments_ciphertext is None and kept.arguments_sha256 is not None
    (shown,) = (await client.get(f"{ONE}/calls")).json()["items"]
    assert (shown["state"], shown["outcome"], shown["instance_id"], shown["epoch"]) == (
        "in_doubt",
        "unanswered",
        instance,
        0,
    )


async def test_an_instances_sequence_holds_a_clock_in_milliseconds(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    """A daemon raises its sequence to its clock in milliseconds, and the contract lets it go up to
    2^53 - 1: the row keeps 64 bits, where 32 would refuse the first request of any daemon."""
    from choregos_api.db.models import OutpostInstance
    from choregos_api.db.session import name_the_outpost, session_scope
    from sqlalchemy import BigInteger

    assert isinstance(OutpostInstance.__table__.c.sequence.type, BigInteger)
    await kit.create(client)
    instance = await kit.enrolled_instance(client)
    row = await kit.outpost_row(SLUG)
    for sequence in (int(time.time() * 1000), 2**53 - 1):
        async with session_scope(orgs="*") as session:
            await name_the_outpost(session, row.id)
            taken = await session.get(OutpostInstance, instance)
            assert taken is not None
            taken.sequence = sequence
            await session.flush()
        (kept,) = await kit.rows_of(SLUG, OutpostInstance)
        assert kept.sequence == sequence


def _keyring() -> Any:
    import os

    from choregos_api.secret_store import Keyring

    return Keyring(keys={"k1": os.urandom(32)}, active_id="k1")


async def test_a_calls_arguments_wait_sealed_and_open_on_their_own_row_only(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    """Encrypted under the key wrapper while they wait, emptied when the call completes; the digest
    stays (ADR 0103, ADR 0148 §11)."""
    import os

    from choregos_api.db.models import OutpostCall
    from choregos_api.secret_store import Keyring, SecretStoreUnavailable
    from choregos_api.services import outposts as service

    await kit.create(client)
    row = await kit.outpost_row(SLUG)
    keyring = Keyring(keys={"k1": os.urandom(32)}, active_id="k1")
    arguments = {"query": "status = open AND assignee = ada@example.com", "limit": 20}

    def call(call_id: str = "call-1", outpost_id: str = row.id, org_id: str = row.org_id) -> OutpostCall:
        return OutpostCall(id=call_id, org_id=org_id, outpost_id=outpost_id, kind="call_tool",
                           endpoint="tracker", tool="search_issues", write=False)  # fmt: skip

    waiting = call()
    assert service.open_call_arguments(waiting, keyring) is None, "a row that holds none"
    service.seal_call_arguments(waiting, arguments, keyring)
    assert waiting.arguments_sha256 == contracts.arguments_sha256(arguments)
    assert waiting.arguments_kek_id == "k1"
    at_rest = b"".join(
        getattr(waiting, name)
        for name in (
            "arguments_ciphertext",
            "arguments_nonce",
            "arguments_wrapped_dek",
            "arguments_dek_nonce",
        )
    )
    assert b"ada@example.com" not in at_rest and b"status" not in at_rest
    assert service.open_call_arguments(waiting, keyring) == arguments

    def moved_to(other: OutpostCall) -> OutpostCall:
        for name in ("ciphertext", "nonce", "wrapped_dek", "dek_nonce", "kek_id"):
            setattr(other, f"arguments_{name}", getattr(waiting, f"arguments_{name}"))
        return other

    for elsewhere in (
        call("call-2"),
        call(outpost_id="another-outpost"),
        call(org_id="another-organisation"),
    ):
        with pytest.raises(SecretStoreUnavailable):
            service.open_call_arguments(moved_to(elsewhere), keyring)
    with pytest.raises(SecretStoreUnavailable):
        service.open_call_arguments(waiting, Keyring(keys={"k1": os.urandom(32)}, active_id="k1"))

    service.clear_call_arguments(waiting)
    assert service.open_call_arguments(waiting, keyring) is None
    assert waiting.arguments_ciphertext is None and waiting.arguments_wrapped_dek is None
    assert waiting.arguments_sha256 == contracts.arguments_sha256(arguments), "the digest stays"


async def test_without_a_key_wrapper_no_argument_is_written(
    client: AsyncClient, admin: str, door: kit.Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In clear is not a fallback: with no key configured the arguments are not put on the row."""
    from choregos_api.db.models import OutpostCall
    from choregos_api.secret_store import ENV_KEKS, SecretStoreUnavailable, active_key_wrapper
    from choregos_api.services import outposts as service

    monkeypatch.delenv(ENV_KEKS, raising=False)
    assert active_key_wrapper() is None
    waiting = OutpostCall(id="call-1", org_id="o", outpost_id="p", kind="call_tool", endpoint="tracker")
    with pytest.raises(SecretStoreUnavailable):
        service.seal_call_arguments(waiting, {"query": "open"})
    assert waiting.arguments_ciphertext is None and waiting.arguments_sha256 is None

    import base64
    import os

    monkeypatch.setenv(ENV_KEKS, "k7:" + base64.b64encode(os.urandom(32)).decode())
    service.seal_call_arguments(waiting, {"query": "open"})
    assert waiting.arguments_kek_id == "k7", "the deployment's own keyring, when it has one"
    assert service.open_call_arguments(waiting) == {"query": "open"}

    # The wrapper an extension supplies with its store (ADR 0103) is the one a call is sealed under.
    from choregos_api.secret_store import FakeSecretStore, Keyring, SealedDbSecretStore, set_secret_store

    try:
        set_secret_store(SealedDbSecretStore(keyring=Keyring(keys={"kms": os.urandom(32)}, active_id="kms")))
        service.seal_call_arguments(waiting, {"query": "closed"})
        assert waiting.arguments_kek_id == "kms"
        assert service.open_call_arguments(waiting) == {"query": "closed"}
        set_secret_store(FakeSecretStore())
        assert active_key_wrapper() is None, "the in-memory fake seals nothing"
    finally:
        set_secret_store(None)


def test_an_enrolment_token_is_one_the_doors_contract_accepts() -> None:
    """The token carries its outpost, and stays inside what `enrol` takes (`outpost-door.yaml`)."""
    from choregos_api.db.models import Outpost
    from choregos_api.services import outposts as service

    outpost = Outpost(id="0192f0c0-1111-7222-8333-444455556666", org_id="o", slug=SLUG, display_name=SLUG)
    token, digest = service._new_token(outpost)
    assert re.fullmatch(r"[A-Za-z0-9._~-]{16,256}", token)
    assert service.outpost_id_of_token(token) == outpost.id
    assert digest == service.token_digest(token) and len(digest) == 64 and token not in digest
    other, _ = service._new_token(outpost)
    assert other != token and len(token) >= len(service.ENROLMENT_TOKEN_PREFIX) + 32 + 43
    instance = service._new_instance_id(outpost)
    assert re.fullmatch(contracts.outpost.OUTPOST_ID_PATTERN, instance) and len(instance) == 64
    assert service.outpost_id_of_instance(instance) == outpost.id
    for unreadable in ("", "short", "Z" * 64, "0192f0c0-1111-7222-8333-444455556666"):
        assert service.outpost_id_of_instance(unreadable) is None
        assert service.outpost_id_of_token(unreadable) is None
