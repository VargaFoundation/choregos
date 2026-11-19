"""Web Push: a person's devices are told, over the standard protocol (S81-03, ADR 0194).

The crypto is checked against the RFC's own example and by decrypting what the sender produced; the
routes and the delivery against a fake push service that scripts the answers."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

import httpx
import jwt
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient
from sqlalchemy import select
from tranibor_api.config import reset_settings_cache
from tranibor_api.db.models import PushSubscription
from tranibor_api.db.session import ALL_ORGS, session_scope
from tranibor_api.push import endpoints
from tranibor_api.push import sender as senders
from tranibor_api.push.delivery import Notice, deliver, wait_idle
from tranibor_api.push.endpoints import EndpointRefusedError, check_endpoint
from tranibor_api.push.sender import FakePushSender, HttpPushSender, Target
from tranibor_api.push.webpush import PushCryptoError, VapidKey, b64u, decrypt, encrypt, unb64u

from .agent_scopes_kit import ADMIN, ORG, add_member, as_user, make_project
from .test_agent_social import OTHER, _publish, _watched_reviewer

PUBLIC = "93.184.216.34"
OWNER, DEV, VIEWER = "owner@northwind.dev", "dev@northwind.dev", "viewer@northwind.dev"


class Phone:
    """What a device holds: its key pair and its auth secret, to decrypt what it is sent."""

    def __init__(self, name: str) -> None:
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.auth = b64u(b"0123456789abcdef")
        self.p256dh = b64u(
            self.private.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        )
        self.endpoint = f"https://push.example.test/send/{name}"

    def body(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
            "device_name": "Pixel",
        }


@pytest.fixture(autouse=True)
def _push_world(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakePushSender]:
    """A resolver that answers a public address, and a fake push service the sender is swapped for."""

    async def resolve(host: str) -> list[str]:
        return [PUBLIC]

    monkeypatch.setattr(endpoints, "_resolve", resolve)
    fake = FakePushSender()
    monkeypatch.setattr(senders, "_override", fake)
    yield fake
    reset_settings_cache()


# ───────────────────────────── the protocol ─────────────────────────────

RFC_PLAINTEXT = b"When I grow up, I want to be a watermelon"
RFC_UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
RFC_UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A"
    "_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)


def _scalar(text: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(unb64u(text), "big"), ec.SECP256R1())


def test_the_encryption_is_the_one_of_rfc_8291_appendix_a() -> None:
    body = encrypt(
        RFC_PLAINTEXT,
        RFC_UA_PUBLIC,
        RFC_AUTH,
        sender_key=_scalar("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"),
        salt=unb64u("DGv6ra1nlYgDCS1FRnbzlw"),
    )
    assert b64u(body) == RFC_BODY
    assert decrypt(unb64u(RFC_BODY), _scalar(RFC_UA_PRIVATE), RFC_AUTH) == RFC_PLAINTEXT


def test_what_is_encrypted_is_read_by_the_device_only_and_is_fresh_each_time() -> None:
    phone, thief = Phone("a"), Phone("b")
    first = encrypt(b'{"title":"x"}', phone.p256dh, phone.auth)
    assert decrypt(first, phone.private, phone.auth) == b'{"title":"x"}'
    assert first != encrypt(b'{"title":"x"}', phone.p256dh, phone.auth)
    with pytest.raises((InvalidTag, PushCryptoError)):
        decrypt(first, thief.private, phone.auth)
    with pytest.raises((InvalidTag, PushCryptoError)):
        decrypt(first, phone.private, b64u(b"another secret!!"))
    tampered = bytearray(first)
    tampered[-1] ^= 1
    with pytest.raises((InvalidTag, PushCryptoError)):
        decrypt(bytes(tampered), phone.private, phone.auth)


def test_a_key_or_a_payload_the_protocol_cannot_carry_is_refused() -> None:
    phone = Phone("a")
    with pytest.raises(PushCryptoError):
        encrypt(b"x", b64u(b"not a point"), phone.auth)
    with pytest.raises(PushCryptoError):
        encrypt(b"x", phone.p256dh, b64u(b"short"))
    with pytest.raises(PushCryptoError):
        encrypt(b"x" * 4096, phone.p256dh, phone.auth)


def test_the_vapid_token_is_signed_by_the_installation_for_the_push_service() -> None:
    key = VapidKey(ec.generate_private_key(ec.SECP256R1()))
    header = key.headers("https://push.example.test", "mailto:ops@example.test", now=1_000_000)[
        "Authorization"
    ]
    scheme, _, rest = header.partition(" ")
    parts = dict(item.split("=", 1) for item in rest.split(", "))
    assert scheme == "vapid" and parts["k"] == key.public_key
    claims = jwt.decode(
        parts["t"],
        key.private.public_key(),
        algorithms=["ES256"],
        audience="https://push.example.test",
        options={"verify_exp": False},
    )
    assert claims == {
        "aud": "https://push.example.test",
        "exp": 1_000_000 + 12 * 3600,
        "sub": "mailto:ops@example.test",
    }
    other = ec.generate_private_key(ec.SECP256R1()).public_key()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            parts["t"],
            other,
            algorithms=["ES256"],
            audience="https://push.example.test",
            options={"verify_exp": False},
        )


def test_the_key_is_read_as_a_pem_or_as_the_raw_scalar() -> None:
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    raw = b64u(private.private_numbers().private_value.to_bytes(32, "big"))
    assert VapidKey.load(pem).public_key == VapidKey.load(raw).public_key
    for bad in ("garbage", b64u(b"short")):
        with pytest.raises(PushCryptoError):
            VapidKey.load(bad)


async def test_the_http_sender_posts_what_only_the_device_can_read() -> None:
    phone = Phone("http")
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    key = VapidKey(ec.generate_private_key(ec.SECP256R1()))
    sender = HttpPushSender(key, "mailto:ops@example.test", transport=httpx.MockTransport(answer))
    status = await sender.send(
        Target(phone.endpoint, phone.p256dh, phone.auth), {"title": "hello"}, urgency="high"
    )
    assert status == 201
    (request,) = seen
    assert request.headers["content-encoding"] == "aes128gcm" and request.headers["urgency"] == "high"
    assert request.headers["authorization"].startswith("vapid t=")
    assert json.loads(decrypt(request.content, phone.private, phone.auth)) == {"title": "hello"}


# ───────────────────────────── the endpoint is the client's ─────────────────────────────


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://push.example.test/x",
        "https://user:pw@push.example.test/x",
        "https://127.0.0.1/x",
        "https://10.0.0.5/x",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/x",
        "https://push.example.test:8443/x",
        "https:///x",
        "https://push.example.test/" + "a" * 2100,
    ],
)
async def test_an_endpoint_that_is_not_a_public_https_host_is_refused(endpoint: str) -> None:
    with pytest.raises(EndpointRefusedError):
        await check_endpoint(endpoint)


async def test_a_host_that_resolves_to_a_private_address_is_refused_unless_the_operator_allows_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def inside(host: str) -> list[str]:
        return [PUBLIC, "10.1.2.3"]  # one private answer among public ones is enough to refuse

    monkeypatch.setattr(endpoints, "_resolve", inside)
    with pytest.raises(EndpointRefusedError):
        await check_endpoint("https://rebind.example.test/x")
    await check_endpoint("https://gateway.internal/x", allow_private=True)


# ───────────────────────────── the routes ─────────────────────────────


async def _register(client: AsyncClient, email: str, phone: Phone, **extra: Any) -> httpx.Response:
    await as_user(client, email)
    return await client.post("/api/v1/me/push-subscriptions", json={**phone.body(), **extra})


async def test_an_instance_without_a_key_says_so_and_stores_nothing(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(senders, "_override", None)
    monkeypatch.setenv("CHOREGOS_FAKES", "false")
    reset_settings_cache()
    await as_user(client, ADMIN)
    key = await client.get("/api/v1/push/vapid-public-key")
    assert key.status_code == 404 and key.json()["code"] == "push_not_configured"
    created = await client.post("/api/v1/me/push-subscriptions", json=Phone("a").body())
    assert created.status_code == 409 and created.json()["code"] == "push_not_configured"
    assert (await client.get("/api/v1/me/push-subscriptions")).json() == []


async def test_the_key_is_served_to_a_signed_in_person_only(client: AsyncClient, org: str) -> None:
    assert (await client.get("/api/v1/push/vapid-public-key")).status_code == 401
    await as_user(client, ADMIN)
    served = await client.get("/api/v1/push/vapid-public-key")
    assert served.status_code == 200
    assert len(unb64u(served.json()["public_key"])) == 65


async def test_a_device_registers_once_and_the_endpoint_is_never_echoed(
    client: AsyncClient, org: str
) -> None:
    phone = Phone("a")
    first = await _register(client, ADMIN, phone)
    assert first.status_code == 201, first.text
    again = await client.post(
        "/api/v1/me/push-subscriptions", json={**phone.body(), "device_name": "Renamed"}
    )
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    listed = (await client.get("/api/v1/me/push-subscriptions")).json()
    assert [(d["device_name"], d["endpoint_host"]) for d in listed] == [("Renamed", "push.example.test")]
    assert sorted(listed[0]["kinds"]) == sorted(
        ["decision.awaiting", "run.ended", "agent.notification", "session.ended"]
    )
    assert "/send/a" not in json.dumps([first.json(), again.json(), listed])


@pytest.mark.parametrize(
    "mutation",
    [
        {"endpoint": "http://push.example.test/x"},
        {"endpoint": "https://127.0.0.1/x"},
        {"keys": {"p256dh": "AAAA", "auth": "AAAA"}},
        {"kinds": ["everything"]},
        {"device_name": ""},
    ],
)
async def test_a_registration_the_protocol_or_the_platform_cannot_serve_is_refused(
    client: AsyncClient, org: str, mutation: dict[str, Any]
) -> None:
    await as_user(client, ADMIN)
    refused = await client.post("/api/v1/me/push-subscriptions", json={**Phone("a").body(), **mutation})
    assert refused.status_code == 422, refused.text
    assert (await client.get("/api/v1/me/push-subscriptions")).json() == []


async def test_a_machine_does_not_register_a_device(client: AsyncClient, org: str) -> None:
    await as_user(client, ADMIN)
    token = await client.post("/api/v1/me/tokens", json={"name": "ci", "scopes": ["mcp:read"]})
    secret = token.json()["token"]
    client.cookies.clear()
    refused = await client.post(
        "/api/v1/me/push-subscriptions", json=Phone("a").body(), headers={"Authorization": f"Bearer {secret}"}
    )
    assert refused.status_code == 403


async def test_devices_are_the_persons_own_and_nobody_else_lists_tests_or_deletes_them(
    client: AsyncClient, org: str
) -> None:
    await add_member(client, DEV, "developer")
    mine = (await _register(client, ADMIN, Phone("admin"))).json()
    await as_user(client, DEV)
    assert (await client.get("/api/v1/me/push-subscriptions")).json() == []
    assert (await client.delete(f"/api/v1/me/push-subscriptions/{mine['id']}")).status_code == 404
    assert (await client.post(f"/api/v1/me/push-subscriptions/{mine['id']}/test")).status_code == 404
    await as_user(client, ADMIN)
    assert len((await client.get("/api/v1/me/push-subscriptions")).json()) == 1
    assert (await client.delete(f"/api/v1/me/push-subscriptions/{mine['id']}")).status_code == 204
    assert (await client.delete(f"/api/v1/me/push-subscriptions/{mine['id']}")).status_code == 404


async def test_an_endpoint_another_person_held_moves_to_the_caller(client: AsyncClient, org: str) -> None:
    await add_member(client, DEV, "developer")
    phone = Phone("shared")
    assert (await _register(client, ADMIN, phone)).status_code == 201
    moved = await _register(client, DEV, phone)
    assert moved.status_code == 200, moved.text
    assert (await client.get("/api/v1/me/push-subscriptions")).json()[0]["id"] == moved.json()["id"]
    await as_user(client, ADMIN)
    assert (await client.get("/api/v1/me/push-subscriptions")).json() == []


async def test_the_test_message_reaches_the_device_and_a_gone_device_is_removed(
    client: AsyncClient, org: str, _push_world: FakePushSender
) -> None:
    phone = Phone("t")
    created = (await _register(client, ADMIN, phone)).json()
    ok = await client.post(f"/api/v1/me/push-subscriptions/{created['id']}/test")
    assert ok.status_code == 200 and ok.json() == {"delivered": True, "status_code": 201, "removed": False}
    (payload,) = _push_world.to(phone.endpoint)
    assert payload["kind"] == "push.test" and payload["ref"] == {"type": "none", "id": created["id"]}
    _push_world.answers[phone.endpoint] = 410
    gone = await client.post(f"/api/v1/me/push-subscriptions/{created['id']}/test")
    assert gone.json() == {"delivered": False, "status_code": 410, "removed": True}
    assert (await client.get("/api/v1/me/push-subscriptions")).json() == []


async def test_a_failure_other_than_gone_is_counted_not_fatal(
    client: AsyncClient, org: str, _push_world: FakePushSender
) -> None:
    phone = Phone("f")
    created = (await _register(client, ADMIN, phone)).json()
    _push_world.answers[phone.endpoint] = 503
    failed = await client.post(f"/api/v1/me/push-subscriptions/{created['id']}/test")
    assert failed.json() == {"delivered": False, "status_code": 503, "removed": False}
    _push_world.broken.add(phone.endpoint)
    unreachable = await client.post(f"/api/v1/me/push-subscriptions/{created['id']}/test")
    assert unreachable.json() == {"delivered": False, "status_code": None, "removed": False}
    (listed,) = (await client.get("/api/v1/me/push-subscriptions")).json()
    assert listed["last_failure_at"] is not None and listed["last_delivered_at"] is None


# ───────────────────────────── who is told ─────────────────────────────


async def _org_id() -> str:
    from tranibor_api.db.models import Organization

    async with session_scope(orgs=ALL_ORGS) as session:
        return (await session.execute(select(Organization.id))).scalar_one()


async def _stored() -> list[PushSubscription]:
    async with session_scope(orgs=ALL_ORGS) as session:
        return list((await session.execute(select(PushSubscription))).scalars())


async def test_a_dead_device_is_forgotten_a_failing_one_kept_then_dropped_after_30_days(
    client: AsyncClient, org: str, _push_world: FakePushSender
) -> None:
    from datetime import timedelta

    from tranibor_core import utcnow

    gone, flaky = Phone("gone"), Phone("flaky")
    await _register(client, ADMIN, gone)
    await _register(client, ADMIN, flaky)
    notice = Notice(
        kind="run.ended", org_id=await _org_id(), title="t", body="b", ref_type="run", ref_id="r", path="/x"
    )
    _push_world.answers[gone.endpoint] = 410
    _push_world.answers[flaky.endpoint] = 500
    assert await deliver(notice, sender=_push_world) == 0
    (left,) = await _stored()
    assert left.endpoint == flaky.endpoint and left.last_failure_at is not None
    async with session_scope(orgs=ALL_ORGS) as session:
        row = (await session.execute(select(PushSubscription))).scalar_one()
        row.failing_since = utcnow() - timedelta(days=31)
    await deliver(notice, sender=_push_world)
    assert await _stored() == []


async def _decision_world(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, Phone]:
    """A project, four people with a device each, and the directory a proposed action calls."""
    import tranibor_adapters
    from tranibor_adapters.fakes.entra import FakeEntra

    monkeypatch.setattr(tranibor_adapters, "FAKE_ENTRA", FakeEntra())
    monkeypatch.setenv("CONNECTOR_ENTRA_CLIENT_SECRET", "connector-secret")
    await as_user(client, ADMIN)
    project = await make_project(client, "billing")
    connector = {
        "name": "entra-acme",
        "type": "entra",
        "config": {"tenant_id": "acme", "client_id": "choregos"},
        "secret_refs": {"client_secret": "env:CONNECTOR_ENTRA_CLIENT_SECRET"},
    }
    assert (await client.post(f"{ORG}/connectors", json=connector)).status_code == 201
    await add_member(client, OWNER, "project_owner")
    await add_member(client, DEV, "developer")
    await add_member(client, VIEWER, "viewer")
    phones = {email: Phone(email.split("@")[0]) for email in (ADMIN, OWNER, DEV, VIEWER, "stranger@else.dev")}
    for email, phone in phones.items():
        if email != "stranger@else.dev":
            assert (await _register(client, email, phone)).status_code == 201
    phones["pid"] = project["id"]  # type: ignore[assignment]
    return phones


def _propose(client: AsyncClient, project_id: str, **extra: Any) -> Any:
    effects = [
        {
            "effect": "connector.call",
            "with": {"connector": "entra-acme", "operation": "create_user", "arguments": {"upn": "a@b.test"}},
        }
    ]
    body = {
        "kind": "entra.create_user",
        "title": "Create an account",
        "params": {},
        "effects": effects,
        **extra,
    }
    return client.post(f"/api/v1/projects/{project_id}/actions", json=body)


async def test_a_decision_awaiting_tells_the_people_who_may_decide_and_nobody_else(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch, _push_world: FakePushSender
) -> None:
    phones = await _decision_world(client, monkeypatch)
    await as_user(client, DEV)
    proposed = await _propose(client, str(phones["pid"]))  # type: ignore[arg-type]
    assert proposed.status_code == 201, proposed.text
    await wait_idle()
    told = {e for e, p in phones.items() if isinstance(p, Phone) and _push_world.to(p.endpoint)}
    assert told == {ADMIN, OWNER}, (
        "the proposer (a developer) and a viewer cannot decide; a stranger has no right"
    )
    (payload,) = _push_world.to(phones[OWNER].endpoint)
    assert payload["kind"] == "decision.awaiting" and payload["ref"]["id"] == proposed.json()["id"]
    assert payload["org"] == "northwind" and payload["url"].startswith("/")
    assert set(payload) == {"kind", "title", "body", "org", "ref", "url", "at"}
    assert "a@b.test" not in json.dumps(payload), "the action's parameters are not in the push"
    assert {u for _, _, u in _push_world.sent} == {"high"}


async def test_a_person_who_proposes_is_not_told_of_their_own_proposal(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch, _push_world: FakePushSender
) -> None:
    phones = await _decision_world(client, monkeypatch)
    await as_user(client, ADMIN)
    assert (await _propose(client, str(phones["pid"]))).status_code == 201  # type: ignore[arg-type]
    await wait_idle()
    assert not _push_world.to(phones[ADMIN].endpoint)  # type: ignore[union-attr]
    assert _push_world.to(phones[OWNER].endpoint)  # type: ignore[union-attr]


async def test_a_rule_that_names_a_group_sends_no_push(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch, _push_world: FakePushSender
) -> None:
    phones = await _decision_world(client, monkeypatch)
    await as_user(client, DEV)
    rule = {"approvers": [{"role": "project_owner", "min": 1, "group": "netops-approvers"}]}
    assert (await _propose(client, str(phones["pid"]), approval=rule)).status_code == 201  # type: ignore[arg-type]
    await wait_idle()
    assert _push_world.sent == []


async def test_a_device_gets_only_the_kinds_it_asked_for(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch, _push_world: FakePushSender
) -> None:
    phones = await _decision_world(client, monkeypatch)
    quiet = Phone("quiet")
    assert (await _register(client, OWNER, quiet, kinds=["run.ended"])).status_code == 201
    await as_user(client, DEV)
    assert (await _propose(client, str(phones["pid"]))).status_code == 201  # type: ignore[arg-type]
    await wait_idle()
    assert not _push_world.to(quiet.endpoint)
    assert _push_world.to(phones[OWNER].endpoint)  # type: ignore[union-attr]


async def test_an_action_settled_before_the_send_is_not_announced(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch, _push_world: FakePushSender
) -> None:
    phones = await _decision_world(client, monkeypatch)
    await as_user(client, DEV)
    action = (await _propose(client, str(phones["pid"]))).json()  # type: ignore[arg-type]
    await wait_idle()
    _push_world.sent.clear()
    from tranibor_api.db.models import Action

    async with session_scope(orgs=ALL_ORGS) as session:
        (await session.get(Action, action["id"])).status = "approved"  # type: ignore[union-attr]
    notice = Notice(
        kind="decision.awaiting",
        org_id=await _org_id(),
        title="t",
        body="b",
        ref_type="action",
        ref_id=action["id"],
        path="/x",
        action_id=action["id"],
    )
    assert await deliver(notice, sender=_push_world) == 0 and _push_world.sent == []


async def test_a_push_that_fails_never_fails_what_it_announces(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch, _push_world: FakePushSender
) -> None:
    phones = await _decision_world(client, monkeypatch)
    for phone in phones.values():
        if isinstance(phone, Phone):
            _push_world.broken.add(phone.endpoint)
    await as_user(client, DEV)
    proposed = await _propose(client, str(phones["pid"]))  # type: ignore[arg-type]
    assert proposed.status_code == 201
    await wait_idle()
    assert (
        await client.get(f"/api/v1/projects/{phones['pid']}/actions/{proposed.json()['id']}")
    ).status_code == 200


async def test_a_watcher_is_told_of_what_happens_to_the_agent_they_watch(
    client: AsyncClient, org: str, _push_world: FakePushSender
) -> None:
    await _watched_reviewer(client)
    phone = Phone("watcher")
    assert (await _register(client, OTHER, phone)).status_code == 201
    await as_user(client, ADMIN)
    await _publish(client, "reviewer", 2)
    await wait_idle()
    kinds = {p["kind"] for p in _push_world.to(phone.endpoint)}
    assert kinds == {"agent.notification"}
    assert all(p["ref"]["type"] == "agent" for p in _push_world.to(phone.endpoint))


async def test_a_watcher_who_left_the_organisation_is_not_told(
    client: AsyncClient, org: str, _push_world: FakePushSender
) -> None:
    await _watched_reviewer(client)
    phone = Phone("gone-watcher")
    assert (await _register(client, OTHER, phone)).status_code == 201
    from tranibor_api.db.models import Membership

    async with session_scope(orgs=ALL_ORGS) as session:
        for row in (await session.execute(select(Membership))).scalars():
            if row.user.email == OTHER:
                await session.delete(row)
    await as_user(client, ADMIN)
    await _publish(client, "reviewer", 2)
    await wait_idle()
    assert _push_world.to(phone.endpoint) == []


def test_a_key_that_is_there_without_a_subject_is_not_a_working_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(senders, "_override", None)
    monkeypatch.setenv("CHOREGOS_FAKES", "false")
    monkeypatch.setenv("CHOREGOS_PUSH_VAPID_PRIVATE_KEY", b64u(b"\x01" * 32))
    monkeypatch.setenv("CHOREGOS_PUSH_VAPID_SUBJECT", "")
    reset_settings_cache()
    from tranibor_api.config import get_settings

    assert senders.public_key(get_settings()) is None
    monkeypatch.setenv("CHOREGOS_PUSH_VAPID_SUBJECT", "mailto:ops@example.test")
    reset_settings_cache()
    assert senders.public_key(get_settings()) == VapidKey.load(b64u(b"\x01" * 32)).public_key
    assert isinstance(senders.get_sender(get_settings()), HttpPushSender)


def test_the_encoding_helpers_agree_with_the_standard_library() -> None:
    raw = bytes(range(70))
    assert base64.urlsafe_b64decode(b64u(raw) + "==") == raw and unb64u(b64u(raw)) == raw
