"""The door that is served is the door of the contract (S65-12, ADR 0148 §2).

`packages/contracts/outpost-door.yaml` is what a daemon nobody at the platform upgrades is built
against. Three things are held to it here: the routes the application serves are its four verbs and
no other; the contract's vectors, which a door and a daemon are both built against, pass through the
mounted door; and a call the door hands over is signed as the document says, to the byte.

The vectors were not made by this door: their instance id is not one it would have given, and their
nonce is opaque (`v1.1794477600.example-nonce`). So the door of this test is one that gave them: its
clock stands at the vectors' `iat`, the MAC it computes for that second is the vectors', and the
vectors' instance id names the test's outpost. Everything else is the door's own code: the
signature, what it binds, the window of `iat`, the sequence, the schema, the verb.

The bound on a handed call (`x-bounds.max_sealed_call_bytes`) and the refusal codes, `cannot_record`
among them (S65-10b), are the contract's own: the door reads both from `choregos_contracts`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import choregos_contracts as contracts
import pytest
import yaml
from choregos_adapters import sealing
from choregos_contracts import MAX_BODY_BYTES, NONCE_HEADER, SIGNATURE_HEADER, outpost
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from httpx import AsyncClient

from .kits import outpost_daemon as daemon_kit
from .kits import outposts as kit
from .kits.outpost_daemon import SLUG, Daemon, b64, refused

DOOR: dict[str, Any] = contracts.load_outpost_door()
EXAMPLES = contracts.schemas_dir() / "examples"
SIGNED: dict[str, Any] = json.loads((EXAMPLES / "outpost-signature.vector.json").read_text("utf-8"))
SEALED: dict[str, Any] = json.loads((EXAMPLES / "outpost-sealing.vector.json").read_text("utf-8"))
REQUESTS: dict[str, dict[str, Any]] = {request["name"]: request for request in SIGNED["requests"]}
#: The second for which the vectors were signed.
VECTOR_TIME: int = REQUESTS["poll"]["claims"]["iat"]
_RAW = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _seed(label: str) -> bytes:
    """The vectors' private material: the SHA-256 of a label written in them, never a key."""
    return hashlib.sha256(label.encode("utf-8")).digest()


def _door_pem() -> str:
    key = Ed25519PrivateKey.from_private_bytes(_seed(SEALED["door"]["signing_key_label"]))
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> str:
    """The door of the vectors: their origin, and the signing key their label gives, which is the one
    the policy example pins. Its public half is returned."""
    daemon_kit.key_wrapper(monkeypatch)
    kit.configure(
        monkeypatch,
        outposts="on",
        outposts_door_url=kit.ORIGIN,
        outposts_door_signing_key=_door_pem(),
    )
    return str(SEALED["door"]["signing_key"])


# ───────────────────────── the routes ─────────────────────────


def _served(app: Any) -> set[tuple[str, str]]:
    from fastapi.routing import iter_route_contexts

    return {
        (method, context.path)
        for context in iter_route_contexts(app.router.routes)
        for method in (context.methods or ())
        if (context.path or "").startswith("/outposts")
    }


def test_the_live_routes_are_the_contracts_four_verbs(app: Any) -> None:
    declared = {
        (method.upper(), path)
        for path, verbs in DOOR["paths"].items()
        for method in verbs
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert len(declared) == 4 and {method for method, _ in declared} == {"POST"}
    assert _served(app) == declared, "the door serves the four verbs of its contract, and no other"
    assert all(path.startswith(outpost.OUTPOST_DOOR_PREFIX + "/") for _, path in declared)
    # Outside the API's document: the door has a contract of its own.
    assert not [path for path in app.openapi()["paths"] if "outposts/v1" in path]
    assert not [path for path in contracts.load_openapi()["paths"] if "outposts/v1" in path]


def test_with_outposts_off_the_application_serves_none_of_them(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.main import create_app

    kit.configure(monkeypatch, outposts="off")
    assert _served(create_app()) == set()


def test_the_bounds_the_door_applies_are_the_documents() -> None:
    from choregos_api.outposts import auth, rendezvous
    from choregos_api.outposts import door as served

    bounds = DOOR["x-bounds"]
    assert bounds["max_body_bytes"] == MAX_BODY_BYTES
    assert {path: verbs["post"]["x-max-body-bytes"] for path, verbs in DOOR["paths"].items()} == {
        "/outposts/v1/enrol": MAX_BODY_BYTES["enrol"],
        "/outposts/v1/poll": MAX_BODY_BYTES["poll"],
        "/outposts/v1/calls/{id}/answer": MAX_BODY_BYTES["answer"],
        "/outposts/v1/calls/{id}/refuse": MAX_BODY_BYTES["refuse"],
    }
    assert (bounds["iat_window_s"], bounds["nonce_ttl_s"]) == (outpost.IAT_WINDOW_S, outpost.NONCE_TTL_S)
    assert (bounds["max_wait_s"], bounds["max_lease_s"]) == (outpost.MAX_WAIT_S, outpost.MAX_LEASE_S)
    assert bounds["max_concurrency"] == outpost.MAX_CONCURRENCY == 64
    # A lease is the document's and nothing longer; a poll's slot outlives its wait by a few seconds.
    assert served.SLOT_MARGIN_S > 0 and max(rendezvous.LOOK_S) <= 10 and min(rendezvous.LOOK_S) >= 5
    assert DOOR["x-protocol"]["version"] == auth.PROTOCOL_CURRENT == outpost.OUTPOST_PROTOCOL
    assert auth.PROTOCOL_MINIMUM <= auth.PROTOCOL_CURRENT
    # One element of `calls`, 2 MiB as carried; at most 256 cancellations, which is what a poll may say
    # it holds (S65-10b).
    assert bounds["max_sealed_call_bytes"] == rendezvous.MAX_SEALED_CALL_BYTES == 2 * 1024 * 1024
    running = DOOR["components"]["schemas"]["PollRequest"]["properties"]["running"]
    assert served.MAX_CANCEL == running["maxItems"] == 256


# ───────────────────────── the signature a call is handed over with ─────────────────────────


def test_the_door_signs_a_call_as_the_vector_is_signed_to_the_byte(door: str) -> None:
    """`x-call-signature`: a compact JWS, `EdDSA`, `kid` the signing key itself, the three claims and
    no other. Ed25519 leaves nothing to chance: the door's signature of the vector's call is the
    vector's."""
    from choregos_api.config import ed25519_public_key
    from choregos_api.outposts import rendezvous

    call = SEALED["call"]
    assert ed25519_public_key(_door_pem()) == door
    policy = yaml.safe_load((EXAMPLES / "outpost-policy.example.yaml").read_text("utf-8"))
    assert policy["door_keys"] == [door], "the key the policy example pins"
    signature = rendezvous.sign_call(
        _door_pem(), call_id=call["associated_data"], instance_id=call["instance"], sealed=call["sealed"]
    )
    assert signature == call["signature"]
    handed = {"id": call["associated_data"], "sealed": call["sealed"], "signature": signature}
    assert (
        handed == json.loads((EXAMPLES / "outpost-poll-answer.example.json").read_text("utf-8"))["calls"][0]
    )
    assert Daemon(instance_id=call["instance"]).verifies(handed, policy["door_keys"])


# ───────────────────────── the nonce ─────────────────────────


def test_a_nonce_is_an_authenticated_timestamp_the_door_checks_alone() -> None:
    """Issued without stored state (RFC 9449 §8): with a key and a clock that are fixed, so is the
    nonce; it is taken for 300 seconds by any door that holds the key, and by no other."""
    from choregos_api.outposts import auth

    key = _seed("choregos tests: a nonce key")
    issued = auth.Nonces(key).issue(at=VECTOR_TIME)
    assert issued == auth.Nonces(key).issue(at=VECTOR_TIME + 0.9), "the same second, the same nonce"
    version, seconds, mac = issued.split(".")
    assert (version, int(seconds), len(mac)) == ("v1", VECTOR_TIME, 43)
    header = DOOR["components"]["headers"]["OutpostNonce"]["schema"]
    assert header["minLength"] <= len(issued) <= header["maxLength"]
    another_replica = auth.Nonces(key)
    assert another_replica.fresh(issued, at=VECTOR_TIME)
    assert another_replica.fresh(issued, at=VECTOR_TIME + outpost.NONCE_TTL_S)
    assert not another_replica.fresh(issued, at=VECTOR_TIME + outpost.NONCE_TTL_S + 1)
    assert not auth.Nonces(_seed("another door")).fresh(issued, at=VECTOR_TIME)
    for forged in (
        f"v1.{VECTOR_TIME + 1}.{mac}",
        f"v2.{VECTOR_TIME}.{mac}",
        f"v1.{VECTOR_TIME}",
        "",
        None,
        7,
    ):
        assert not another_replica.fresh(forged, at=VECTOR_TIME), forged
    # The key is derived from the door's signing key: replicas that hold the one agree on the other.
    assert auth._nonce_key(_door_pem()) == auth._nonce_key(_door_pem()) != key


# ───────────────────────── the vectors, through the mounted door ─────────────────────────


@pytest.fixture
async def vectors_door(client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """A door that gave the vectors their instance id and their nonce, at the second they were signed.
    The vectors' instance is an approved instance of the test's outpost; its id is returned."""
    from choregos_api.db.models import OutpostInstance
    from choregos_api.db.session import name_the_outpost, session_scope
    from choregos_api.outposts import auth
    from choregos_api.services import outposts as service

    await kit.create(client, SLUG)
    row = await kit.outpost_row(SLUG)
    instance_id = str(SIGNED["instance_id"])
    async with session_scope(orgs="*") as session:
        await name_the_outpost(session, row.id)
        session.add(
            OutpostInstance(
                id=instance_id,
                org_id=row.org_id,
                outpost_id=row.id,
                status="active",
                signing_key=SIGNED["signing_key"],
                sealing_key=SEALED["call"]["recipient_key"],
                fingerprint=SIGNED["fingerprint"],
                daemon_version="0.26.0",
                capabilities=["tools"],
                endpoints=[],
            )
        )
    # The vectors' id is not one this door gives (its ids start with their outpost's): it names ours.
    carried = service.outpost_id_of_instance
    monkeypatch.setattr(
        service, "outpost_id_of_instance", lambda kid: row.id if kid == instance_id else carried(kid)
    )

    class VectorNonces(auth.Nonces):
        """The door whose MAC of the vectors' second is the vectors' opaque `example-nonce`."""

        def _mac(self, issued: int) -> str:
            return "example-nonce" if issued == VECTOR_TIME else super()._mac(issued)

    key = auth._nonce_key(_door_pem())
    monkeypatch.setattr(auth, "nonces", lambda _settings: VectorNonces(key))
    monkeypatch.setattr(auth, "clock", lambda: VECTOR_TIME + 2.0)
    return instance_id


async def _send(client: AsyncClient, request: dict[str, Any], **changed: Any) -> Any:
    """A vector's request as it was sent: its exact body, its signature, its path."""
    sent = request | changed
    path = sent["url"].removeprefix(kit.ORIGIN)
    return await client.post(
        path, content=sent["body"].encode("utf-8"), headers={SIGNATURE_HEADER: sent["signature"]}
    )


async def test_the_vectors_enrolment_is_signed_as_the_door_verifies_it(
    client: AsyncClient, vectors_door: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signed with the key it enrols, no `kid`, nonce or sequence. Its token is an example's: the door
    gets as far as the token, which is after the signature, what it binds and its time."""
    from choregos_api.outposts import auth

    enrol = REQUESTS["enrol"]
    refused(await _send(client, enrol), "invalid_enrolment_token", 401)
    one_byte_off = enrol["body"].replace('"capabilities":["tools"]', '"capabilities":[]')
    refused(await _send(client, enrol, body=one_byte_off), "request_mismatch", 401)
    refused(await _send(client, enrol, signature=REQUESTS["poll"]["signature"]), "invalid_signature", 401)
    monkeypatch.setattr(auth, "clock", lambda: VECTOR_TIME + 61.0)
    stale = refused(await _send(client, enrol), "stale_iat", 401)
    assert stale["server_time"] == "2026-11-12T10:01:01Z", "the door's clock, for a daemon whose own drifts"


async def test_the_vectors_requests_pass_the_door_in_their_order(
    client: AsyncClient, vectors_door: str, door: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A poll under sequence 41, an answer under 42, a refusal under 43: each is taken for what the
    contract says it is, and the calls the poll is handed are the door's, signed and sealed."""
    from choregos_api.outposts import rendezvous

    poll, answer, refuse = REQUESTS["poll"], REQUESTS["answer"], REQUESTS["refuse"]
    written = json.loads(SEALED["call"]["plaintext"])
    answered_id, refused_id = written["id"], json.loads(poll["body"])["running"][0]
    assert answer["url"].endswith(f"/calls/{answered_id}/answer")
    assert refuse["url"].endswith(f"/calls/{refused_id}/refuse")
    # The calls the vectors answer, placed as whoever routes them would; the first one's answer key
    # is the vectors', so that the vectors' sealed answer is one its caller can open.
    answer_key = X25519PrivateKey.from_private_bytes(_seed(SEALED["answer"]["recipient_key_label"]))
    assert b64(answer_key.public_key().public_bytes(*_RAW)) == written["answer_key"]
    with monkeypatch.context() as patched:
        patched.setattr(rendezvous.X25519PrivateKey, "generate", staticmethod(lambda: answer_key))
        await daemon_kit.place(
            call_id=answered_id,
            tool=written["tool"],
            arguments=written["arguments"],
            write=True,
            asserted=written["asserted"],
            timeout_s=60,
        )
    await daemon_kit.place(call_id=refused_id)

    before = datetime.now(UTC)
    response = await _send(client, poll)
    assert response.status_code == 200, response.text
    polled = response.json()
    daemon_kit.is_a("PollAnswer", polled)
    outpost.OutpostPollAnswer.model_validate(polled)
    assert polled["server_time"] == "2026-11-12T10:00:02Z" and polled["cancel"] == [] and not polled["stop"]
    assert response.headers[NONCE_HEADER].startswith(f"v1.{VECTOR_TIME + 2}.")

    # Every call handed over is signed by the door, with the key the policy example pins, and is
    # sealed to the vectors' instance: verified as the contract tells a daemon to, then opened.
    instance = Daemon(
        instance_id=vectors_door,
        sealing=X25519PrivateKey.from_private_bytes(_seed(SEALED["call"]["recipient_key_label"])),
    )
    assert [call["id"] for call in polled["calls"]] == [answered_id, refused_id]
    calls = {handed["id"]: instance.opened(handed, pinned=[door]) for handed in polled["calls"]}
    for handed in polled["calls"]:
        assert handed["signature"] == rendezvous.sign_call(
            _door_pem(), call_id=handed["id"], instance_id=vectors_door, sealed=handed["sealed"]
        )
        assert rendezvous.carried_bytes(handed) <= rendezvous.MAX_SEALED_CALL_BYTES
    opened = calls[answered_id]
    deadline = datetime.fromisoformat(opened.pop("deadline"))
    assert 0 < (deadline - before).total_seconds() <= 61
    # What the instance reads is what was placed, to the field; the deadline is this door's own.
    assert opened == {name: value for name, value in written.items() if name != "deadline"}

    taken = await _send(client, answer)
    assert (taken.status_code, taken.json()) == (200, {"id": answered_id, "recorded": "answer"})
    daemon_kit.is_a("CallAck", taken.json())
    turned_down = await _send(client, refuse)
    assert (turned_down.status_code, turned_down.json()) == (200, {"id": refused_id, "recorded": "refusal"})
    assert (await daemon_kit.call_row(refused_id)).refusal == json.loads(refuse["body"])["code"]

    # The door stored the vectors' envelope as it came, and whoever placed the call opens it.
    row = await daemon_kit.call_row(answered_id)
    assert row.answer_sealed == json.loads(answer["body"])["sealed"] == SEALED["answer"]["sealed"]
    outcome = await rendezvous.wait(row.outpost_id, answered_id)
    assert outcome is not None and outcome.answer is not None
    assert outcome.answer.model_dump(mode="json", exclude_none=True) == json.loads(
        SEALED["answer"]["plaintext"]
    )
    assert sealing.open_sealed(answer_key, answered_id, row.answer_sealed) == json.loads(
        SEALED["answer"]["plaintext"]
    )

    # Each took its sequence: the same requests, sent again, are replays, in any order.
    assert (await daemon_kit.instance_row(vectors_door)).sequence == refuse["claims"]["seq"] == 43
    for request in (refuse, poll, answer):
        refused(await _send(client, request), "replayed_sequence", 401)


async def test_a_vector_bent_one_way_at_a_time_is_refused_for_that_way(
    client: AsyncClient, vectors_door: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same request, with one thing wrong: the code says which, and it took no sequence."""
    from choregos_api.outposts import auth

    poll = REQUESTS["poll"]
    refused(await _send(client, poll, url=REQUESTS["refuse"]["url"]), "request_mismatch", 401)
    refused(
        await _send(client, poll, body=poll["body"].replace('"capacity":7', '"capacity":8')),
        "request_mismatch",
        401,
    )
    tampered = poll["signature"][:-4] + ("AAAA" if not poll["signature"].endswith("AAAA") else "BBBB")
    refused(await _send(client, poll, signature=tampered), "invalid_signature", 401)
    monkeypatch.setattr(auth, "clock", lambda: VECTOR_TIME + 61.0)
    refused(await _send(client, poll), "stale_iat", 401)
    monkeypatch.setattr(auth, "clock", lambda: VECTOR_TIME + 2.0)
    monkeypatch.setattr(
        auth, "nonces", lambda settings: auth.Nonces(auth._nonce_key(settings.outposts_door_signing_key))
    )
    stale = await _send(client, poll)
    refused(stale, "stale_nonce", 401)  # a door that did not issue the vectors' nonce
    assert stale.headers[NONCE_HEADER].startswith(f"v1.{VECTOR_TIME + 2}.")
    assert (await daemon_kit.instance_row(vectors_door)).sequence == 0, "none of them took a sequence"
