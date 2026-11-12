# SPDX-License-Identifier: Apache-2.0
"""A call is signed by the door, and an outpost makes no other (ADR 0148 §13, `x-call-signature`).

Sealing hides a call and proves no origin: whoever terminates the daemon's TLS knows the instance's
public sealing key and can seal a call of its own. So before it opens an envelope, the daemon checks
the door's signature against the keys its own policy file pins. A failure is one refusal,
`signature_refused`: the envelope stays closed, the server is not called, and the one audit line
names no tool.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_adapters import sealing
from choregos_contracts import outpost_door_path, schemas_dir
from choregos_contracts.outpost import REQUEST_SIGNATURE_TYPE, OutpostCall, OutpostSealedCall
from choregos_outpost import identity as identities
from choregos_outpost.calls import signed_by
from choregos_outpost.daemon import Exit
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from ._bench import Bench, bench

EXAMPLES = schemas_dir() / "examples"
VECTOR: dict[str, Any] = json.loads((EXAMPLES / "outpost-sealing.vector.json").read_text(encoding="utf-8"))
SIGNED: dict[str, Any] = json.loads((EXAMPLES / "outpost-signature.vector.json").read_text(encoding="utf-8"))


def _derived(label: str) -> bytes:
    """The vectors' private material: the SHA-256 of a label, test material anyone can derive."""
    return hashlib.sha256(label.encode("utf-8")).digest()


def _vector_call() -> OutpostSealedCall:
    poll = json.loads((EXAMPLES / "outpost-poll-answer.example.json").read_text(encoding="utf-8"))
    return OutpostSealedCall.model_validate(poll["calls"][0])


def _pinned() -> list[str]:
    policy = yaml.safe_load((EXAMPLES / "outpost-policy.example.yaml").read_text(encoding="utf-8"))
    pinned: list[str] = policy["door_keys"]
    return pinned


def test_the_contracts_own_call_is_verified_and_opened_by_the_daemons_code() -> None:
    """The vector both programs are built against: the call of the poll's example, signed by the key the
    policy example pins, sealed to the instance whose keys the vectors derive from their labels."""
    assert outpost_door_path().exists()
    handed, instance = _vector_call(), SIGNED["instance_id"]
    assert signed_by(handed, pinned=_pinned(), instance_id=instance) == VECTOR["door"]["signing_key"]
    own = identities.Identity(
        Ed25519PrivateKey.from_private_bytes(_derived(SIGNED["signing_key_label"])),
        X25519PrivateKey.from_private_bytes(_derived(VECTOR["call"]["recipient_key_label"])),
    )
    assert own.signing_key == SIGNED["signing_key"] and own.fingerprint == SIGNED["fingerprint"]
    assert own.sealing_key == VECTOR["call"]["recipient_key"]
    opened = own.open(handed.id, handed.sealed)
    assert opened == json.loads(VECTOR["call"]["plaintext"])
    call = OutpostCall.model_validate(opened)
    assert (call.id, call.endpoint, call.tool) == (handed.id, "tracker", "create_issue")


def test_each_check_of_the_contract_failing_alone_is_a_refusal() -> None:
    handed, instance, pinned = _vector_call(), SIGNED["instance_id"], _pinned()
    other = "A" + pinned[0][1:]

    def altered(text: str) -> str:
        return text[:-1] + ("A" if text[-1] != "A" else "B")

    refused = {
        "no signature": handed.model_copy(update={"signature": None}),
        "a signature that is no JWS": handed.model_copy(update={"signature": "garbage"}),
        "a changed signature": handed.model_copy(update={"signature": altered(handed.signature or "")}),
        "a changed envelope": handed.model_copy(update={"sealed": altered(handed.sealed)}),
        "another call's id": handed.model_copy(update={"id": "another-call"}),
        "a request's signature, the instance's own": handed.model_copy(
            update={"signature": SIGNED["requests"][1]["signature"]}
        ),
    }
    for what, call in refused.items():
        assert signed_by(call, pinned=pinned, instance_id=instance) is None, what
    assert signed_by(handed, pinned=pinned, instance_id="another-instance") is None
    assert signed_by(handed, pinned=[], instance_id=instance) is None
    assert signed_by(handed, pinned=[other], instance_id=instance) is None, "a key the file does not pin"
    # A rotation under way: several keys are pinned, and the call verifies under the one it names.
    assert signed_by(handed, pinned=[other, *pinned], instance_id=instance) == pinned[0]


FORGERIES = {
    "unsigned": {"drop": True},
    "a key nobody pinned": {"signed_with": "a forger"},
    "a key nobody pinned, under the pinned name": {"signed_with": "a forger", "kid": "pinned"},
    "the request signature's type": {"header": {"typ": REQUEST_SIGNATURE_TYPE}},
    "the default type": {"header": {"typ": "JWT"}},
    "one claim more": {"iat": 1_700_000_000},
    "a deadline of its own": {"deadline": "2099-01-01T00:00:00Z"},
    "another instance": {"instance": "another-instance"},
    "another envelope": {"sealed_sha256": "0" * 64},
    "another call": {"call": "another-call"},
}


def _forged(b: Bench, call_id: str, how: dict[str, Any]) -> dict[str, Any]:
    how = dict(how)
    if how.pop("drop", False):
        document = b.handed(call_id)
        del document["signature"]
        return document
    if how.get("signed_with") == "a forger":
        how["signed_with"] = Ed25519PrivateKey.generate()
    if how.get("kid") == "pinned":
        how["kid"] = b.door.public_key
    return b.handed(call_id, **how)


@pytest.mark.parametrize("how", FORGERIES.values(), ids=FORGERIES.keys())
async def test_a_call_the_door_did_not_sign_is_refused_without_being_opened(
    tmp_path: Path, daemon_log: io.StringIO, monkeypatch: pytest.MonkeyPatch, how: dict[str, Any]
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    opened: list[str] = []
    real_open = identities.Identity.open

    def spy(self: identities.Identity, call_id: str, sealed: str) -> dict[str, Any]:
        opened.append(call_id)
        return real_open(self, call_id, sealed)

    monkeypatch.setattr(identities.Identity, "open", spy)
    # The forged call is sealed to the instance: it would open. Its plaintext names a tool the policy
    # allows: it would be made.
    b.door.hand_as_is(_forged(b, "forged-0001", how))
    real = b.door.submit("tracker", "search_issues", {"query": "real"})
    await b.start()
    await b.door.answered(real.id)
    await asyncio.wait_for(_refusal_sent(b, "forged-0001"), timeout=5)
    assert await b.stop() is Exit.STOPPED
    assert opened == [real.id], "the forged envelope was not opened"
    assert b.arrived == ["search_issues"] and b.server.calls == [("search_issues", {"query": "real"})]
    # One line, which says nothing of what the envelope held: no key verified it, and no tool is named.
    assert b.lines("forged-0001") == [
        {
            "at": b.lines("forged-0001")[0]["at"],
            "event": "refused",
            "call_id": "forged-0001",
            "refusal": "signature_refused",
        }
    ]
    # The refusal names the class, never the check. At the door a forged call's refusal meets a 404.
    sent = [json.loads(body) for verb, claims, body in b.door.received if verb == "refuse" and claims]
    assert {"code": "signature_refused"} in sent
    assert "forged-0001" not in b.door.refusals and "forged-0001" not in b.door.answers


async def _refusal_sent(b: Bench, call_id: str) -> None:
    while not any(
        verb == "refuse" and claims and claims["htu"].endswith(f"/calls/{call_id}/refuse")
        for verb, claims, _ in b.door.received
    ):
        await asyncio.sleep(0.01)


async def test_a_call_signed_by_the_second_pinned_key_is_made(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    """A rotation: the operator added the next key before the door signs with it."""
    b = await bench(tmp_path, daemon_log)
    next_key = Ed25519PrivateKey.generate()
    from choregos_outpost.fakes.door import _RAW, _b64

    pinned = [b.door.public_key, _b64(next_key.public_key().public_bytes(*_RAW))]
    b.policy = b.policy.model_copy(update={"door_keys": pinned})
    plaintext = {
        "id": "rotated-0001",
        "kind": "call_tool",
        "endpoint": "tracker",
        "tool": "search_issues",
        "arguments": {"query": "rotation"},
        "deadline": "2099-01-01T00:00:00Z",
        "answer_key": b.identity.sealing_key,
    }
    b.door.hand_as_is(b.handed("rotated-0001", plaintext, signed_with=next_key))
    await b.start()
    await asyncio.wait_for(_executed(b, "rotated-0001"), timeout=5)
    await b.stop()
    assert b.server.calls == [("search_issues", {"query": "rotation"})]
    assert b.lines("rotated-0001")[0]["signed_by"] == pinned[1]


async def _executed(b: Bench, call_id: str) -> None:
    while not any(line["event"] == "executed" for line in b.lines(call_id)):
        await asyncio.sleep(0.01)


async def test_an_envelope_that_does_not_open_is_unreadable(tmp_path: Path, daemon_log: io.StringIO) -> None:
    """Signed by the door, and sealed to another key: the signature is verified, nothing is read."""
    b = await bench(tmp_path, daemon_log)
    _, stranger = sealing.new_key()
    sealed = sealing.seal(stranger, "unreadable-0001", {"id": "unreadable-0001", "kind": "list_tools"})
    signature = b.door.signature_for("unreadable-0001", b.instance_id, sealed)
    b.door.hand_as_is({"id": "unreadable-0001", "sealed": sealed, "signature": signature})
    await b.start()
    await asyncio.wait_for(_refusal_sent(b, "unreadable-0001"), timeout=5)
    await b.stop()
    received, refused = b.lines("unreadable-0001")
    assert received["signed_by"] == b.door.public_key and "kind" not in received
    assert refused["refusal"] == "call_unreadable" and b.server.received == []


async def test_a_call_whose_inside_names_another_id_is_unreadable(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    inside = {
        "id": "another-id",
        "kind": "call_tool",
        "endpoint": "tracker",
        "tool": "search_issues",
        "arguments": {},
        "deadline": "2099-01-01T00:00:00Z",
        "answer_key": b.identity.sealing_key,
    }
    b.door.hand_as_is(b.handed("outside-id", inside))
    await b.start()
    await asyncio.wait_for(_refusal_sent(b, "outside-id"), timeout=5)
    await b.stop()
    assert b.lines("outside-id")[1]["refusal"] == "call_unreadable" and b.server.received == []
