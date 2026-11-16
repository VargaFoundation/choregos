# SPDX-License-Identifier: Apache-2.0
"""The uplink signs exactly what `x-request-signature` describes (ADR 0148 §4).

The contract ships signed requests of one instance, with nothing left to chance: a key derived from
a label, a time, a nonce, three sequence numbers, four bodies. Given the same, the daemon's own code
sends the same four `Outpost-Signature` headers, byte for byte, over the same four bodies.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from choregos_contracts import schemas_dir
from choregos_contracts.enums import OutpostDoorCode, OutpostRefusalCode
from choregos_contracts.outpost import NONCE_HEADER, SIGNATURE_HEADER
from choregos_outpost import identity as identities
from choregos_outpost import uplink
from choregos_outpost.fakes import FakeDoor
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from ._bench import bench

EXAMPLES = schemas_dir() / "examples"
SIGNED: dict[str, Any] = json.loads((EXAMPLES / "outpost-signature.vector.json").read_text(encoding="utf-8"))
SEALED: dict[str, Any] = json.loads((EXAMPLES / "outpost-sealing.vector.json").read_text(encoding="utf-8"))
VECTORS = {request["name"]: request for request in SIGNED["requests"]}


def _derived(label: str) -> bytes:
    return hashlib.sha256(label.encode("utf-8")).digest()


def _vector_identity() -> identities.Identity:
    """The instance of the contract's vectors: its keys are the SHA-256 of labels written there."""
    return identities.Identity(
        Ed25519PrivateKey.from_private_bytes(_derived(SIGNED["signing_key_label"])),
        X25519PrivateKey.from_private_bytes(_derived(SEALED["call"]["recipient_key_label"])),
    )


class _Scripted(uplink.Sequence):
    """The sequence numbers the vector was signed under, in order."""

    def __init__(self, values: list[int]) -> None:
        super().__init__()
        self._values = values

    async def next(self) -> int:
        return self._values.pop(0)


async def test_the_daemon_signs_the_contracts_four_requests_byte_for_byte() -> None:
    sent: list[httpx.Request] = []
    answers = {
        "/outposts/v1/enrol": json.loads(
            (EXAMPLES / "outpost-enrolment-answer.example.json").read_text("utf-8")
        ),
        "/outposts/v1/poll": json.loads((EXAMPLES / "outpost-poll-answer.example.json").read_text("utf-8")),
    }
    ack = json.loads((EXAMPLES / "outpost-call-ack.example.json").read_text("utf-8"))

    def door(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        status = 201 if request.url.path.endswith("/enrol") else 200
        # The nonce the vector's later requests were signed with.
        return httpx.Response(
            status,
            json=answers.get(request.url.path, ack),
            headers={NONCE_HEADER: VECTORS["poll"]["claims"]["nonce"]},
        )

    iat = VECTORS["poll"]["claims"]["iat"]
    http = uplink.client(transport=httpx.MockTransport(door))
    facts = json.loads(VECTORS["enrol"]["body"])["facts"]
    enroller = uplink.Uplink(
        "https://outposts.example.com", _vector_identity(), http, now=lambda: iat, daemon_version="0.26.0"
    )
    enrolled = await enroller.enrol(json.loads(VECTORS["enrol"]["body"])["token"], facts)
    assert (
        enrolled.instance_id == SIGNED["instance_id"]
        and enrolled.fingerprint == _vector_identity().fingerprint
    )

    door_client = uplink.Uplink(
        "https://outposts.example.com",
        _vector_identity(),
        http,
        instance_id=enrolled.instance_id,
        sequence=_Scripted([request["claims"]["seq"] for request in SIGNED["requests"][1:]]),
        now=lambda: iat,
        daemon_version="0.26.0",
    )
    door_client._nonce = VECTORS["poll"]["claims"]["nonce"]
    poll = json.loads(VECTORS["poll"]["body"])
    await door_client.poll(
        wait_s=poll["wait_s"], capacity=poll["capacity"], endpoints=poll["endpoints"], running=poll["running"]
    )
    answer_id = VECTORS["answer"]["url"].split("/")[-2]
    await door_client.answer(answer_id, json.loads(VECTORS["answer"]["body"])["sealed"])
    refuse_id = VECTORS["refuse"]["url"].split("/")[-2]
    await door_client.refuse(refuse_id, OutpostRefusalCode(json.loads(VECTORS["refuse"]["body"])["code"]))
    await http.aclose()

    assert [request.method for request in sent] == ["POST"] * 4
    for request, vector in zip(sent, SIGNED["requests"], strict=True):
        assert str(request.url) == vector["url"], vector["name"]
        assert request.content.decode("utf-8") == vector["body"], vector["name"]
        assert request.headers[SIGNATURE_HEADER] == vector["signature"], vector["name"]
        assert jwt.get_unverified_header(request.headers[SIGNATURE_HEADER]) == vector["header"]


async def test_the_first_signed_request_has_no_nonce_and_is_signed_again_with_the_refusals(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    door_client = b.uplink()
    answer = await door_client.poll(wait_s=0, capacity=1, endpoints=["tracker"], running=[])
    await door_client.aclose()
    assert answer.calls == [] and answer.protocol.minimum == 1
    polls = [(claims, body) for verb, claims, body in b.door.received if verb == "poll"]
    assert len(polls) == 2, "refused `stale_nonce`, then taken"
    (first, body), (second, same_body) = polls
    assert first is not None and second is not None
    assert "nonce" not in first and b.door._fresh(second["nonce"])
    # Signed again, never sent again: a higher sequence, the same request.
    assert second["seq"] > first["seq"] and body == same_body
    assert (first["htm"], first["htu"]) == ("POST", "https://outposts.example.com/outposts/v1/poll")
    assert first["body_sha256"] == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert list(second) == ["htm", "htu", "body_sha256", "iat", "nonce", "seq"]
    assert b.door.instances[b.instance_id].sequence == second["seq"]


async def test_a_request_the_door_took_a_higher_sequence_before_is_signed_again_under_a_higher_one(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    door_client = b.uplink()
    await door_client.poll(wait_s=0, capacity=0, endpoints=[], running=[])
    # Two requests crossed: the door answers `replayed_sequence` once.
    b.door.refuse_next("poll", OutpostDoorCode.REPLAYED_SEQUENCE)
    before = b.door.polls
    await door_client.poll(wait_s=0, capacity=0, endpoints=[], running=[])
    await door_client.aclose()
    sequences = [claims["seq"] for verb, claims, _ in b.door.received if verb == "poll" and claims][-2:]
    assert b.door.polls == before + 2 and sequences[1] > sequences[0]


async def test_a_refusal_that_signing_again_does_not_cure_is_handed_over(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    door_client = b.uplink()
    b.door.refuse_next("poll", OutpostDoorCode.REPLAYED_SEQUENCE, times=5)
    with pytest.raises(uplink.DoorRefusedError) as refused:
        await door_client.poll(wait_s=0, capacity=0, endpoints=[], running=[])
    await door_client.aclose()
    assert refused.value.code is OutpostDoorCode.REPLAYED_SEQUENCE and refused.value.status == 401
    assert refused.value.problem.server_time is not None
    signed = [claims["seq"] for verb, claims, _ in b.door.received if verb == "poll" and claims]
    assert len(signed) == len(set(signed)), "a sequence is never used twice"


async def test_the_enrolment_is_signed_with_the_key_it_enrols_and_ties_the_sealing_key(
    tmp_path: Path,
) -> None:
    door = FakeDoor()
    own = identities.create(tmp_path / "identity")
    async with uplink.client(transport=door.transport()) as http:
        answer = await uplink.Uplink(door.origin, own, http).enrol(
            door.enrolment_token(), {"hostname": "outpost-0"}
        )
    verb, claims, body = door.received[0]
    assert verb == "enrol" and claims is not None
    assert list(claims) == ["htm", "htu", "body_sha256", "iat"], "no nonce, no sequence: none exists yet"
    sent = json.loads(body)
    assert sent["signing_key"] == own.signing_key and sent["sealing_key"] == own.sealing_key
    assert (
        sent["capabilities"] == ["tools"]
        and sent["protocol"] == 1
        and sent["facts"] == {"hostname": "outpost-0"}
    )
    assert answer.status == "pending" and answer.fingerprint == own.fingerprint
    assert door.instances[answer.instance_id].status == "pending"

    # A proxy that swapped the sealing key under the same signature would have to forge it.
    swapped = json.dumps(sent | {"sealing_key": "A" * 43}, separators=(",", ":")).encode()
    signature = own.sign(claims, {"alg": "EdDSA", "typ": "choregos-outpost+jwt"})
    door.tokens[sent["token"]] = door.now() + 60
    response = await door.handle(
        httpx.Request(
            "POST", door.origin + "/outposts/v1/enrol", content=swapped, headers={SIGNATURE_HEADER: signature}
        )
    )
    assert response.status_code == 401 and response.json()["code"] == "request_mismatch"


async def test_a_sequence_is_raised_with_every_signature_and_survives_a_restart_and_a_clock_set_back(
    tmp_path: Path,
) -> None:
    clock = [1_800_000_000.0]
    path = tmp_path / "sequence"
    sequence = uplink.Sequence(path, now=lambda: clock[0])
    first = [await sequence.next() for _ in range(3)]
    assert first == sorted(set(first)) and first[0] == 1_800_000_000_000
    # A restart, and a clock that went back a day: the file is the floor.
    clock[0] -= 86_400
    restarted = uplink.Sequence(path, now=lambda: clock[0])
    assert await restarted.next() == first[-1] + 1
    # Without the file, the clock alone keeps a restarted daemon above what it signed before.
    clock[0] += 86_400 + 1
    assert await uplink.Sequence(tmp_path / "another", now=lambda: clock[0]).next() > first[-1]
    assert await sequence.next() < 2**53


async def test_a_sequence_file_the_disk_refuses_is_said_and_does_not_stop_the_daemon(tmp_path: Path) -> None:
    sequence = uplink.Sequence(tmp_path / "missing-directory" / "sequence")
    first = await sequence.next()
    assert sequence.unwritable and await sequence.next() > first


def test_a_backoff_doubles_up_to_its_cap_and_never_waits_the_same_twice() -> None:
    backoff = uplink.Backoff(base=1.0, cap=60.0, rng=random.Random(7))
    waits = [backoff.next() for _ in range(10)]
    ceilings = [1, 2, 4, 8, 16, 32, 60, 60, 60, 60]
    for wait, ceiling in zip(waits, ceilings, strict=True):
        assert ceiling / 2 <= wait <= ceiling
    assert len({round(wait, 6) for wait in waits}) == len(waits), "jitter: no two waits are the same"
    # Two daemons the door dropped at once do not come back at once.
    other = [uplink.Backoff(rng=random.Random(8)).next() for _ in range(1)]
    assert other[0] != waits[0]
    backoff.reset()
    assert backoff.next() <= 1.0


async def test_a_404_that_is_not_the_doors_is_not_taken_for_a_refusal(tmp_path: Path) -> None:
    """Where outposts are switched off the door is not mounted: the daemon meets a 404 that is not one
    of the contract's problems."""
    own = identities.create(tmp_path / "identity")
    http = uplink.client(
        transport=httpx.MockTransport(lambda _: httpx.Response(404, json={"detail": "Not Found"}))
    )
    with pytest.raises(uplink.DoorUnreachableError, match="switched off") as unreachable:
        await uplink.Uplink("https://outposts.example.com", own, http, instance_id="i").poll(
            wait_s=0, capacity=0, endpoints=[], running=[]
        )
    await http.aclose()
    assert unreachable.value.status == 404


async def test_an_answer_above_its_bound_is_not_read(tmp_path: Path) -> None:
    own = identities.create(tmp_path / "identity")
    huge = b'{"protocol":{"current":1,"minimum":1},"padding":"' + b"x" * (2 << 20) + b'"}'
    http = uplink.client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=huge)))
    with pytest.raises(uplink.DoorUnreachableError, match="not read"):
        await uplink.Uplink("https://outposts.example.com", own, http, instance_id="i").poll(
            wait_s=0, capacity=0, endpoints=[], running=[]
        )
    await http.aclose()


async def test_a_redirect_is_not_followed(tmp_path: Path) -> None:
    """The signature binds the URL: a request sent elsewhere would not be the one that was signed."""
    own = identities.create(tmp_path / "identity")
    seen: list[str] = []

    def door(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://elsewhere.example.com/outposts/v1/poll"})

    http = uplink.client(transport=httpx.MockTransport(door))
    with pytest.raises(uplink.DoorUnreachableError):
        await uplink.Uplink("https://outposts.example.com", own, http, instance_id="i").poll(
            wait_s=0, capacity=0, endpoints=[], running=[]
        )
    await http.aclose()
    assert seen == ["https://outposts.example.com/outposts/v1/poll"]
