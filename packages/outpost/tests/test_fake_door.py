# SPDX-License-Identifier: Apache-2.0
"""The fake door is built from the contract: what it answers is what `outpost-door.yaml` describes.

A daemon tested against a door somebody guessed proves nothing about the real one. So the fake's
answers are validated here against the document's own schemas, and its refusals come in the
document's order (`x-request-signature.checks`).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from choregos_adapters import sealing
from choregos_contracts import load_outpost_door, load_schema
from choregos_contracts.enums import OutpostInstanceStatus, OutpostRefusalCode
from choregos_contracts.outpost import (
    DOOR_STATUS,
    MAX_BODY_BYTES,
    NONCE_HEADER,
    SIGNATURE_HEADER,
    OutpostDoorCode,
    call_signature_claims,
    call_signature_header,
)
from choregos_outpost.fakes import FakeDoor
from choregos_outpost.uplink import DoorRefusedError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator

from ._bench import bench

DOOR = load_outpost_door()


def _component(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"components": DOOR["components"], **DOOR["components"]["schemas"][name]},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def _valid(validator: Draft202012Validator, document: Any) -> None:
    faults = [f"{list(error.absolute_path)}: {error.message}" for error in validator.iter_errors(document)]
    assert not faults, faults


async def _post(door: FakeDoor, path: str, body: bytes, signature: str = "") -> httpx.Response:
    headers = {SIGNATURE_HEADER: signature} if signature else {}
    return await door.handle(httpx.Request("POST", door.origin + path, content=body, headers=headers))


async def test_what_the_fake_answers_is_what_the_contract_describes(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    call = b.door.submit("tracker", "search_issues", {"query": "x"})
    seen: list[httpx.Response] = []

    async def recording(request: httpx.Request) -> httpx.Response:
        response = await b.door.handle(request)
        seen.append(response)
        return response

    await b.start(transport=httpx.MockTransport(recording))
    await b.door.answered(call.id)
    await b.stop()
    polls = [r for r in seen if r.status_code == 200 and "calls" in r.json()]
    acks = [r for r in seen if r.status_code == 200 and "recorded" in r.json()]
    problems = [r for r in seen if r.status_code >= 400]
    assert polls and acks and problems
    for response in polls:
        _valid(_component("PollAnswer"), response.json())
        assert response.headers[NONCE_HEADER]
    for response in acks:
        _valid(_component("CallAck"), response.json())
    for response in problems:
        _valid(_component("DoorProblem"), response.json())
        assert response.headers["content-type"] == "application/problem+json"
        assert DOOR_STATUS[OutpostDoorCode(response.json()["code"])] == response.status_code
    # The call it handed over is signed as `x-call-signature` says, by the key a policy file pins.
    handed = next(entry for r in polls for entry in r.json()["calls"])
    _valid(_component("SealedCall"), handed)
    public = Ed25519PublicKey.from_public_bytes(jwt.utils.base64url_decode(b.door.public_key))
    assert jwt.get_unverified_header(handed["signature"]) == call_signature_header(b.door.public_key)
    assert jwt.decode(handed["signature"], public, algorithms=["EdDSA"]) == call_signature_claims(
        call_id=handed["id"], instance_id=b.instance_id, sealed=handed["sealed"]
    )
    # And what was sealed inside is a call of the contract.
    _valid(
        Draft202012Validator(load_schema("outpost-call.schema.json")),
        b.identity.open(handed["id"], handed["sealed"]),
    )


async def test_the_fakes_enrolment_answer_is_the_contracts(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log, approved=False)
    token = b.door.enrolment_token()
    body = json.dumps(
        {
            "protocol": 1,
            "token": token,
            "signing_key": b.identity.signing_key,
            "sealing_key": b.identity.sealing_key,
            "daemon_version": "0.26.0",
            "capabilities": ["tools"],
        }
    ).encode()
    claims = {
        "htm": "POST",
        "htu": b.door.origin + "/outposts/v1/enrol",
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "iat": int(b.door.now()),
    }
    signature = b.identity.sign(claims, {"alg": "EdDSA", "typ": "choregos-outpost+jwt"})
    response = await _post(b.door, "/outposts/v1/enrol", body, signature)
    assert response.status_code == 201
    _valid(Draft202012Validator(load_schema("outpost-enrolment-answer.schema.json")), response.json())
    # A key still pending at the token's outpost is the same instance again, under a new token.
    assert response.json()["instance_id"] == b.instance_id
    # A token enrols one instance, once.
    again = await _post(b.door, "/outposts/v1/enrol", body, signature)
    assert (again.status_code, again.json()["code"]) == (401, "invalid_enrolment_token")
    # A key that was ever approved does not enrol again.
    b.door.approve(b.instance_id)
    body2 = body.replace(token.encode(), b.door.enrolment_token().encode())
    claims["body_sha256"] = hashlib.sha256(body2).hexdigest()
    third = await _post(
        b.door,
        "/outposts/v1/enrol",
        body2,
        b.identity.sign(claims, {"alg": "EdDSA", "typ": "choregos-outpost+jwt"}),
    )
    assert (third.status_code, third.json()["code"]) == (409, "key_already_enrolled")


async def test_the_fake_refuses_in_the_contracts_order(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log)
    door, path = b.door, "/outposts/v1/poll"
    body = json.dumps(
        {"protocol": 1, "daemon_version": "0.26.0", "wait_s": 0, "capacity": 0, "capabilities": ["tools"]}
    ).encode()

    def signed(content: bytes = body, **changed: Any) -> str:
        claims = {
            "htm": "POST",
            "htu": door.origin + path,
            "body_sha256": hashlib.sha256(content).hexdigest(),
            "iat": int(door.now()),
            "nonce": door._nonce(),
            "seq": door.instances[b.instance_id].sequence + 1,
        } | changed
        return b.identity.sign(claims, {"alg": "EdDSA", "typ": "choregos-outpost+jwt", "kid": b.instance_id})

    async def code(content: bytes, signature: str) -> tuple[int, str]:
        response = await _post(door, path, content, signature)
        return response.status_code, response.json()["code"]

    # The body's size comes before anything is parsed, the signature included.
    assert await code(b"x" * (MAX_BODY_BYTES["poll"] + 1), "") == (413, "body_too_large")
    assert await code(body, "") == (401, "invalid_signature")
    assert await code(body, signed(htu=door.origin + "/outposts/v1/enrol", iat=1, nonce="x", seq=0)) == (
        401,
        "request_mismatch",
    )
    assert await code(body, signed(iat=int(door.now()) - 3600, nonce="x", seq=0)) == (401, "stale_iat")
    stale = await _post(door, path, body, signed(nonce="v1.1.forged", seq=0))
    assert (stale.status_code, stale.json()["code"]) == (401, "stale_nonce") and stale.headers[NONCE_HEADER]
    assert await code(body, signed(seq=0)) == (401, "replayed_sequence")
    # A request refused before the sequence check took no sequence.
    assert door.instances[b.instance_id].sequence == 0
    door.instances[b.instance_id].status = OutpostInstanceStatus.PENDING
    assert await code(body, signed()) == (403, "instance_pending")
    door.instances[b.instance_id].status = OutpostInstanceStatus.ACTIVE
    # The protocol is read before the rest of the body is judged.
    door.serve(current=3, minimum=2)
    old = json.dumps({"protocol": 1, "something": "a later shape"}).encode()
    refused = await _post(door, path, old, signed(old))
    assert (refused.status_code, refused.json()["code"]) == (426, "protocol_unsupported")
    assert refused.json()["protocol"] == {"current": 3, "minimum": 2}
    assert refused.headers["upgrade"] == "choregos-outpost/2"
    door.serve(current=1, minimum=1)
    closed = json.dumps({"protocol": 1, "something": "undescribed"}).encode()
    assert await code(closed, signed(closed)) == (422, "invalid_request")
    taken = await _post(door, path, body, signed())
    assert taken.status_code == 200


async def test_one_poll_per_instance_at_a_time(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log)
    b.door.max_wait_s = 30.0
    first, second = b.uplink(), b.uplink()
    await first.poll(wait_s=0, capacity=0, endpoints=[], running=[])
    second._nonce = first._nonce
    waiting = asyncio.create_task(first.poll(wait_s=25, capacity=1, endpoints=[], running=[]))
    async with asyncio.timeout(10):
        while not b.door.instances[b.instance_id].polling:
            await asyncio.sleep(0.005)
    with pytest.raises(DoorRefusedError) as conflict:
        await second.poll(wait_s=0, capacity=1, endpoints=[], running=[])
    # Two holders of one key is what a stolen key looks like: refused, and something to alert on.
    assert conflict.value.code is OutpostDoorCode.POLL_CONFLICT and conflict.value.problem.retry_after == 1
    assert len(b.door.alerts) == 1
    # The poll that waits is answered as soon as the door has a call to hand over.
    call = b.door.submit("tracker", "search_issues", {})
    assert [handed.id for handed in (await asyncio.wait_for(waiting, timeout=10)).calls] == [call.id]
    await first.aclose()
    await second.aclose()


async def test_an_answer_is_taken_from_the_claiming_instance_under_its_epoch_before_the_deadline(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    client = b.uplink()
    call = b.door.submit("tracker", "search_issues", {})
    answer = await client.poll(wait_s=0, capacity=1, endpoints=[], running=[])
    assert [handed.id for handed in answer.calls] == [call.id]
    sealed = sealing.seal(call.plaintext["answer_key"], call.id, {"id": call.id, "outcome": "unanswered"})
    with pytest.raises(DoorRefusedError) as unknown:
        await client.answer("no-such-call", sealed)
    assert unknown.value.code is OutpostDoorCode.NOT_FOUND
    # A revocation moves the epoch: nothing in flight is taken.
    b.door.epoch += 1
    with pytest.raises(DoorRefusedError) as lost:
        await client.refuse(call.id, OutpostRefusalCode.BUSY)
    assert lost.value.code is OutpostDoorCode.LEASE_LOST
    b.door.epoch -= 1
    assert (await client.answer(call.id, sealed)).recorded == "answer"
    # Recorded once: the first of an answer and a refusal wins, whatever is sent after.
    assert (await client.refuse(call.id, OutpostRefusalCode.BUSY)).recorded == "answer"
    assert b.door.answers[call.id].outcome == "unanswered" and call.id not in b.door.refusals
    await client.aclose()


async def test_the_fake_is_served_as_a_bare_asgi_application() -> None:
    door = FakeDoor(origin="http://door.test")
    sent: list[dict[str, Any]] = []
    messages = [{"type": "http.request", "body": b"{}", "more_body": False}]

    async def receive() -> dict[str, Any]:
        return messages.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/outposts/v1/poll",
        "headers": [(b"content-type", b"application/json")],
    }
    await door(scope, receive, send)
    assert sent[0]["status"] == 401 and json.loads(sent[1]["body"])["code"] == "invalid_signature"
    _valid(_component("DoorProblem"), json.loads(sent[1]["body"]))
