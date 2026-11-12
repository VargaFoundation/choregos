# SPDX-License-Identifier: Apache-2.0
"""The outposts' door has a contract of its own (S65-10, ADR 0148): four verbs, a signature, an envelope.

Contract only: the API does not mount the door yet (S65-12), and no daemon exists (S65-15). What is
proven here is what `packages/contracts/outpost-door.yaml` says, on the model of
`test_relay_route_contract.py`: that it declares the four verbs and no other, that nothing in it names
a connector or a server (ADR 0148 §12), that every answer has a schema and every example is valid, and
that the signatures and the envelope it describes are the ones the code already makes
(`choregos_adapters.sealing`, the installed PyJWT). Two signatures cross the door: a request's is the
instance's (ADR 0148 §4), a call's is the door's, under a key the outpost's policy file pins (ADR 0148
§13). The two vectors are what S65-12 and S65-15 are built against: a door and a daemon that agree
with them agree with each other.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import pathlib
import re
from typing import Any

import jsonschema
import jwt
import pytest
import yaml
from choregos_adapters import sealing
from choregos_contracts import outpost
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"
DOOR: dict[str, Any] = yaml.safe_load((CONTRACTS / "outpost-door.yaml").read_text(encoding="utf-8"))
OPENAPI: dict[str, Any] = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
EXAMPLES = CONTRACTS / "schemas" / "examples"
HTTP_VERBS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}

#: The door, frozen by verb and path, parameter names included (ADR 0148 §3, §12). A fifth verb is a
#: `contract-change`, and one every daemon in the field must learn.
VERBS = {
    ("post", "/outposts/v1/enrol"),
    ("post", "/outposts/v1/poll"),
    ("post", "/outposts/v1/calls/{id}/answer"),
    ("post", "/outposts/v1/calls/{id}/refuse"),
}
#: What a route of this door may never name: a door that did would be a passthrough (ADR 0105 §1).
NEVER_NAMED = ("connector", "server", "tool", "mcp")
RAW = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _operations(door: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (verb, path): operation
        for path, item in door["paths"].items()
        for verb, operation in item.items()
        if verb in HTTP_VERBS
    }


def _component(door: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """`node`, or the component of the door's document its `$ref` names."""
    ref = node.get("$ref", "")
    if not ref.startswith("#/"):
        return node
    found: Any = door
    for step in ref.removeprefix("#/").split("/"):
        found = found[step]
    assert isinstance(found, dict)
    return found


def _parameters(door: dict[str, Any], path: str, operation: dict[str, Any]) -> list[dict[str, Any]]:
    declared = [*door["paths"][path].get("parameters", []), *operation.get("parameters", [])]
    return [_component(door, parameter) for parameter in declared]


def _schema_of(content: dict[str, Any]) -> dict[str, Any] | None:
    for media in ("application/json", "application/problem+json"):
        if media in content:
            schema = content[media].get("schema")
            return schema if isinstance(schema, dict) and schema else None
    return None


def faults(door: dict[str, Any]) -> list[str]:
    """What is wrong with a door document; empty for the contract."""
    found: list[str] = []
    if door["info"]["version"] != "v1":
        found.append(f"version {door['info']['version']}")
    declared = set(_operations(door))
    found += [f"verb outside the four: {verb.upper()} {path}" for verb, path in sorted(declared - VERBS)]
    found += [f"verb missing: {verb.upper()} {path}" for verb, path in sorted(VERBS - declared)]
    for (verb, path), operation in _operations(door).items():
        where = f"{verb.upper()} {path}"
        in_path = set(re.findall(r"\{([^}]+)\}", path))
        if in_path - {"id"}:
            found.append(f"{where}: path parameter other than a call's id: {sorted(in_path - {'id'})}")
        if any(word in path.removeprefix("/outposts/v1") for word in NEVER_NAMED):
            found.append(f"{where}: the path names a connector, a server or a tool")
        for parameter in _parameters(door, path, operation):
            name = str(parameter["name"]).lower()
            if any(word in name for word in NEVER_NAMED):
                found.append(f"{where}: parameter `{parameter['name']}` names a connector or a server")
            if parameter["in"] == "path" and parameter["name"] not in in_path:
                found.append(f"{where}: path parameter `{parameter['name']}` is not in the path")
            if parameter["in"] not in {"path"}:
                found.append(f"{where}: parameter `{parameter['name']}` in {parameter['in']}")
        body = operation.get("requestBody", {})
        if not body.get("required") or _schema_of(body.get("content", {})) is None:
            found.append(f"{where}: no required body with a schema")
        if not isinstance(operation.get("x-max-body-bytes"), int):
            found.append(f"{where}: no cap on its body")
        if operation.get("security", door.get("security")) != [{"outpostSignature": []}]:
            found.append(f"{where}: not under the outpost's signature")
        if not any(str(status).startswith("2") for status in operation["responses"]):
            found.append(f"{where}: no success answer")
        for status, response in operation["responses"].items():
            if _schema_of(_component(door, response).get("content", {})) is None:
                found.append(f"{where}: answer {status} has no schema")
    return found


def test_the_door_declares_the_four_verbs_and_nothing_names_a_connector_or_a_server() -> None:
    assert DOOR["openapi"].startswith("3.1")
    assert faults(DOOR) == []
    assert set(_operations(DOOR)) == VERBS
    assert {
        parameter["name"]
        for (_, path), op in _operations(DOOR).items()
        for parameter in _parameters(DOOR, path, op)
    } == {"id"}


def _mutated(change: Any) -> dict[str, Any]:
    door = copy.deepcopy(DOOR)
    change(door)
    return door


def _a_fifth_verb(door: dict[str, Any]) -> None:
    door["paths"]["/outposts/v1/heartbeat"] = {
        "post": copy.deepcopy(door["paths"]["/outposts/v1/poll"]["post"])
    }


def _a_second_method(door: dict[str, Any]) -> None:
    door["paths"]["/outposts/v1/poll"]["get"] = copy.deepcopy(door["paths"]["/outposts/v1/poll"]["post"])


def _a_connector_in_the_path(door: dict[str, Any]) -> None:
    door["paths"]["/outposts/v1/calls/{connector}/answer"] = door["paths"].pop(
        "/outposts/v1/calls/{id}/answer"
    )


def _a_server_parameter(door: dict[str, Any]) -> None:
    door["paths"]["/outposts/v1/poll"]["post"]["parameters"] = [
        {"name": "server", "in": "query", "schema": {"type": "string"}}
    ]


def _a_second_path_parameter(door: dict[str, Any]) -> None:
    door["paths"]["/outposts/v1/calls/{id}/{lane}/answer"] = door["paths"].pop(
        "/outposts/v1/calls/{id}/answer"
    )


def _an_answer_without_a_schema(door: dict[str, Any]) -> None:
    door["paths"]["/outposts/v1/poll"]["post"]["responses"]["409"] = {"description": "Conflict"}


def _a_shared_answer_without_a_schema(door: dict[str, Any]) -> None:
    del door["components"]["responses"]["LeaseLost"]["content"]


def _an_open_verb(door: dict[str, Any]) -> None:
    door["paths"]["/outposts/v1/enrol"]["post"]["security"] = []


def _a_body_without_a_cap(door: dict[str, Any]) -> None:
    del door["paths"]["/outposts/v1/calls/{id}/answer"]["post"]["x-max-body-bytes"]


def _another_version(door: dict[str, Any]) -> None:
    door["info"]["version"] = "v2"


def _a_verb_removed(door: dict[str, Any]) -> None:
    del door["paths"]["/outposts/v1/calls/{id}/refuse"]


@pytest.mark.parametrize(
    ("change", "fault"),
    [
        (_a_fifth_verb, "verb outside the four: POST /outposts/v1/heartbeat"),
        (_a_second_method, "verb outside the four: GET /outposts/v1/poll"),
        (_a_connector_in_the_path, "path parameter other than a call's id: ['connector']"),
        (_a_server_parameter, "parameter `server` names a connector or a server"),
        (_a_second_path_parameter, "path parameter other than a call's id: ['lane']"),
        (_an_answer_without_a_schema, "POST /outposts/v1/poll: answer 409 has no schema"),
        (_a_shared_answer_without_a_schema, "answer 409 has no schema"),
        (_an_open_verb, "POST /outposts/v1/enrol: not under the outpost's signature"),
        (_a_body_without_a_cap, "no cap on its body"),
        (_another_version, "version v2"),
        (_a_verb_removed, "verb missing: POST /outposts/v1/calls/{id}/refuse"),
    ],
)
def test_a_door_that_departs_from_the_four_verbs_is_refused(change: Any, fault: str) -> None:
    found = faults(_mutated(change))
    assert any(fault in line for line in found), found


def test_the_door_is_not_in_the_api_document_and_shares_no_operation_with_it() -> None:
    """ADR 0148 §1: out of the OpenAPI document, like the two other doors."""
    assert not [path for path in OPENAPI["paths"] if path.startswith("/outposts")]
    api = {
        operation["operationId"]
        for item in OPENAPI["paths"].values()
        for verb, operation in item.items()
        if verb in HTTP_VERBS
    }
    door = {operation["operationId"] for operation in _operations(DOOR).values()}
    assert door == {"enrolOutpostInstance", "pollOutpostCalls", "answerOutpostCall", "refuseOutpostCall"}
    assert not door & api
    assert set(DOOR["components"]["securitySchemes"]) == {"outpostSignature"}
    # It speaks no MCP in either direction: no JSON-RPC, no tool listing of the platform.
    text = json.dumps(DOOR["paths"])
    assert "jsonrpc" not in text and "tools/list" not in text and "tools/call" not in text


# ───────────────────────── every example, at the place the document shows it ─────────────────────────


def _validator(schema: dict[str, Any]) -> jsonschema.Draft202012Validator:
    """A validator for a schema of the door's document: a file of `schemas/`, or one of its components."""
    ref = schema.get("$ref", "")
    if ref.startswith("./schemas/"):
        resolved = json.loads((CONTRACTS / ref).read_text(encoding="utf-8"))
    else:
        resolved = {"components": DOOR["components"], **schema}
    return jsonschema.Draft202012Validator(
        resolved, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )


def _shown() -> list[tuple[str, dict[str, Any], str]]:
    """(where, schema, example file) for every example the document shows."""
    found = []
    for (verb, path), operation in _operations(DOOR).items():
        places = [("request", operation["requestBody"])]
        places += [(str(status), _component(DOOR, r)) for status, r in operation["responses"].items()]
        for place, node in places:
            for media in node.get("content", {}).values():
                for example in media.get("examples", {}).values():
                    found.append(
                        (f"{verb.upper()} {path} {place}", media["schema"], example["externalValue"])
                    )
    return found


def test_every_example_the_door_shows_is_valid_where_it_is_shown() -> None:
    shown = _shown()
    assert {where.rsplit(" ", 1)[0] for where, _, _ in shown} == {
        f"{verb.upper()} {path}" for verb, path in VERBS
    }, "a verb shows no example"
    for where, schema, example in shown:
        document = json.loads((CONTRACTS / example).read_text(encoding="utf-8"))
        errors = [f"{list(e.path)}: {e.message}" for e in _validator(schema).iter_errors(document)]
        assert not errors, f"{where}: {example}: {errors}"
    # Every verb shows its request and its success; the door's examples are all shown somewhere.
    places = {where for where, _, _ in shown}
    for verb, path in VERBS:
        assert f"{verb.upper()} {path} request" in places
        assert {f"{verb.upper()} {path} 200", f"{verb.upper()} {path} 201"} & places
    files = {pathlib.Path(example).name for _, _, example in shown}
    door_examples = {
        "outpost-enrolment-request.example.json",
        "outpost-enrolment-answer.example.json",
        "outpost-poll-request.example.json",
        "outpost-poll-answer.example.json",
        "outpost-sealed-answer.example.json",
        "outpost-refusal.example.json",
        "outpost-call-ack.example.json",
        "outpost-door-problem.example.json",
        "outpost-door-problem.protocol.example.json",
    }
    assert files == door_examples


def test_what_a_sealed_field_holds_is_a_schema_that_exists() -> None:
    schemas = DOOR["components"]["schemas"]
    call = schemas["SealedCall"]["properties"]["sealed"]
    answer = schemas["SealedAnswer"]["properties"]["sealed"]
    assert call["x-plaintext"] == {"$ref": "./schemas/outpost-call.schema.json"}
    assert answer["x-plaintext"] == {"$ref": "./schemas/outpost-answer.schema.json"}
    for field in (call, answer):
        assert (CONTRACTS / field["x-plaintext"]["$ref"]).is_file()
    assert "sealing_key" in call["x-sealed-to"] and "answer_key" in answer["x-sealed-to"]
    # A refusal is a code, in clear: nothing of the policy is in it to seal.
    refuse = DOOR["paths"]["/outposts/v1/calls/{id}/refuse"]["post"]["requestBody"]["content"]
    assert refuse["application/json"]["schema"] == {"$ref": "./schemas/outpost-refusal.schema.json"}


# ───────────────────────── the signature ─────────────────────────


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _seed(label: str) -> bytes:
    """The vectors' private material: the SHA-256 of a label, so that no private key is written anywhere."""
    return hashlib.sha256(label.encode("utf-8")).digest()


SIGNED: dict[str, Any] = json.loads(
    (CONTRACTS / DOOR["x-vectors"]["request_signature"]).read_text(encoding="utf-8")
)
SEALED: dict[str, Any] = json.loads((CONTRACTS / DOOR["x-vectors"]["sealing"]).read_text(encoding="utf-8"))


def _signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_seed(SIGNED["signing_key_label"]))


def _verified(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(protected header, claims) of a vector's signature, verified with the instance's public key."""
    public = _signing_key().public_key()
    claims = jwt.decode(
        request["signature"],
        public,
        algorithms=[outpost.SIGNATURE_ALGORITHM],
        options={"verify_iat": False, "verify_exp": False},
    )
    return jwt.get_unverified_header(request["signature"]), claims


def test_the_vector_key_is_the_one_its_label_gives() -> None:
    public = _b64(_signing_key().public_key().public_bytes(*RAW))
    assert public == SIGNED["signing_key"]
    assert outpost.fingerprint_of(public) == SIGNED["fingerprint"]
    digest = hashlib.sha256(_unb64(public)).digest()
    assert SIGNED["fingerprint"] == "SHA256:" + _b64(
        digest
    )  # ADR 0147 §4: the fingerprint of the signing key
    enrolment = json.loads((EXAMPLES / "outpost-enrolment-request.example.json").read_text(encoding="utf-8"))
    assert enrolment["signing_key"] == public


@pytest.mark.parametrize("request_", SIGNED["requests"], ids=[r["name"] for r in SIGNED["requests"]])
def test_a_signed_request_verifies_and_binds_what_the_contract_says(request_: dict[str, Any]) -> None:
    header, claims = _verified(request_)
    enrolment = request_["name"] == "enrol"
    assert header == request_["header"]
    assert header == outpost.request_signature_header(None if enrolment else SIGNED["instance_id"])
    assert claims == request_["claims"]
    # ADR 0148 §4: the method, the URL, a digest of the body, `iat`, the door's nonce, the sequence.
    assert claims == outpost.request_signature_claims(
        method=request_["method"],
        url=request_["url"],
        body=request_["body"].encode("utf-8"),
        iat=claims["iat"],
        nonce=claims.get("nonce"),
        seq=claims.get("seq"),
    )
    assert claims["body_sha256"] == hashlib.sha256(request_["body"].encode("utf-8")).hexdigest()
    assert list(claims) == DOOR["x-request-signature"]["enrolment_claims" if enrolment else "session_claims"]
    assert (verb := (request_["method"].lower(), _path_of(request_["url"]))) in VERBS, verb
    # The body that was signed is a body the verb accepts.
    operation = DOOR["paths"][_path_of(request_["url"])]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert not list(_validator(schema).iter_errors(json.loads(request_["body"])))
    assert len(request_["body"].encode("utf-8")) <= operation["x-max-body-bytes"]


def _path_of(url: str) -> str:
    """The door's path a vector's URL was signed for, its call id put back as `{id}`."""
    policy = yaml.safe_load((EXAMPLES / "outpost-policy.example.yaml").read_text(encoding="utf-8"))
    assert url.startswith(policy["control_plane"] + "/"), "`htu` is the policy's control plane and the path"
    path = url.removeprefix(policy["control_plane"])
    return re.sub(r"(?<=/calls/)[^/]+", "{id}", path)


def test_the_enrolment_is_signed_with_the_key_it_enrols_and_carries_no_session() -> None:
    """The enroller proves it holds the key, and the sealing key is tied to the signing key a person
    compares: a proxy that swapped one of them would have to forge the signature."""
    enrol = next(r for r in SIGNED["requests"] if r["name"] == "enrol")
    header, claims = _verified(enrol)
    assert "kid" not in header and "nonce" not in claims and "seq" not in claims
    body = json.loads(enrol["body"])
    assert body["signing_key"] == SIGNED["signing_key"]
    swapped = json.dumps(body | {"sealing_key": "A" * 43}, separators=(",", ":")).encode("utf-8")
    assert outpost.body_sha256(swapped) != claims["body_sha256"]
    later = [r for r in SIGNED["requests"] if r["name"] != "enrol"]
    assert len(later) == 3
    assert all(r["header"]["kid"] == SIGNED["instance_id"] for r in later)
    sequences = [r["claims"]["seq"] for r in later]
    assert sequences == sorted(set(sequences)), "an instance raises its sequence with every request"


def test_a_signature_over_another_request_does_not_pass_for_this_one() -> None:
    """What S65-12 refuses as `request_mismatch`: a valid signature, bound to something else."""
    poll = next(r for r in SIGNED["requests"] if r["name"] == "poll")
    _, claims = _verified(poll)

    def expected(**changed: Any) -> dict[str, Any]:
        request = {
            "method": poll["method"],
            "url": poll["url"],
            "body": poll["body"].encode("utf-8"),
        } | changed
        return outpost.request_signature_claims(
            **request, iat=claims["iat"], nonce=claims["nonce"], seq=claims["seq"]
        )

    assert claims == expected()
    assert claims != expected(method="GET")
    assert claims != expected(url=poll["url"].replace("/poll", "/enrol"))
    assert claims != expected(url=poll["url"].replace("outposts.example.com", "evil.example.com"))
    assert claims != expected(body=poll["body"].replace('"capacity":7', '"capacity":8').encode("utf-8"))
    tampered = poll["signature"][:-4] + ("AAAA" if not poll["signature"].endswith("AAAA") else "BBBB")
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(tampered, _signing_key().public_key(), algorithms=["EdDSA"], options={"verify_iat": False})
    other = Ed25519PrivateKey.generate().public_key()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(poll["signature"], other, algorithms=["EdDSA"], options={"verify_iat": False})


def test_the_installed_library_signs_what_the_contract_describes() -> None:
    """ADR 0148 needs no new dependency: PyJWT already lists EdDSA, and makes this very signature."""
    assert outpost.SIGNATURE_ALGORITHM in jwt.algorithms.get_default_algorithms()
    for request in SIGNED["requests"]:
        again = jwt.encode(
            request["claims"],
            _signing_key(),
            algorithm=outpost.SIGNATURE_ALGORITHM,
            headers=request["header"],
        )
        header, claims = (
            jwt.get_unverified_header(again),
            jwt.decode(again, options={"verify_signature": False}),
        )
        assert (header, claims) == (request["header"], request["claims"])
        jwt.decode(again, _signing_key().public_key(), algorithms=["EdDSA"], options={"verify_iat": False})


# ───────────────────────── the sealed envelope ─────────────────────────


def _derived_key(shared: bytes, ephemeral: bytes, recipient: bytes) -> bytes:
    """`x-sealing.key_derivation`, written from the document and not from the module."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=ephemeral + recipient,
        info=DOOR["x-sealing"]["info"].encode("utf-8"),
    ).derive(shared)


def _sealed_as_described(vector: dict[str, Any]) -> str:
    recipient = _unb64(vector["recipient_key"])
    ephemeral = X25519PrivateKey.from_private_bytes(_seed(vector["ephemeral_key_label"]))
    ephemeral_public = ephemeral.public_key().public_bytes(*RAW)
    key = _derived_key(
        ephemeral.exchange(X25519PublicKey.from_public_bytes(recipient)), ephemeral_public, recipient
    )
    nonce = _seed(vector["nonce_label"])[:12]
    ciphertext = ChaCha20Poly1305(key).encrypt(
        nonce, vector["plaintext"].encode("utf-8"), vector["associated_data"].encode("utf-8")
    )
    return _b64(ephemeral_public + nonce + ciphertext)


def _opened_as_described(private: X25519PrivateKey, associated_data: str, sealed: str) -> Any:
    raw = _unb64(sealed)
    ephemeral, nonce, ciphertext = raw[:32], raw[32:44], raw[44:]
    recipient = private.public_key().public_bytes(*RAW)
    key = _derived_key(private.exchange(X25519PublicKey.from_public_bytes(ephemeral)), ephemeral, recipient)
    return json.loads(ChaCha20Poly1305(key).decrypt(nonce, ciphertext, associated_data.encode("utf-8")))


def _recipient(vector: dict[str, Any]) -> X25519PrivateKey:
    private = X25519PrivateKey.from_private_bytes(_seed(vector["recipient_key_label"]))
    assert _b64(private.public_key().public_bytes(*RAW)) == vector["recipient_key"]
    return private


@pytest.mark.parametrize("which", ["call", "answer"])
def test_the_envelope_is_the_one_the_sealing_module_makes(which: str) -> None:
    """ADR 0148 §5: `seal` and `open_sealed` as they are, the call's id where a run's id is today."""
    vector = SEALED[which]
    private = _recipient(vector)
    # The document's construction, with the vector's ephemeral key and nonce, gives the vector's bytes…
    assert _sealed_as_described(vector) == vector["sealed"]
    assert re.fullmatch(outpost.SEALED_PATTERN, vector["sealed"])
    # …which the module opens, to the object that was sealed…
    opened = sealing.open_sealed(private, vector["associated_data"], vector["sealed"])
    assert opened == json.loads(vector["plaintext"])
    # …and what the module seals, the document's construction opens.
    fresh = sealing.seal(vector["recipient_key"], vector["associated_data"], opened)
    assert _opened_as_described(private, vector["associated_data"], fresh) == opened
    assert fresh != vector["sealed"], "an envelope has a key and a nonce of its own"
    # Moved to another call, or handed to another key, it does not open.
    with pytest.raises(sealing.SealError):
        sealing.open_sealed(private, "another-call", vector["sealed"])
    with pytest.raises(sealing.SealError):
        sealing.open_sealed(X25519PrivateKey.generate(), vector["associated_data"], vector["sealed"])
    altered = vector["sealed"][:-2] + ("AA" if not vector["sealed"].endswith("AA") else "BB")
    with pytest.raises(sealing.SealError):
        sealing.open_sealed(private, vector["associated_data"], altered)


def test_the_sealing_is_described_as_the_module_does_it() -> None:
    described = DOOR["x-sealing"]
    assert described["info"] == outpost.SEALING_INFO == sealing._INFO.decode()
    assert SEALED["info"] == described["info"]
    assert "choregos_adapters.sealing" in described["construction"]
    for word, where in (
        ("X25519", "key_agreement"),
        ("HKDF-SHA256", "key_derivation"),
        ("ChaCha20-Poly1305", "cipher"),
        ("12-byte", "cipher"),
        ("call's id", "associated_data"),
        ("base64url without padding", "encoding"),
        ("JSON object", "plaintext"),
    ):
        assert word in described[where], (word, where)
    raw = _unb64(SEALED["call"]["sealed"])
    assert raw[:32] == _unb64(SEALED["call"]["ephemeral_key"])
    assert raw[32:44] == _unb64(SEALED["call"]["nonce"])
    assert len(raw) == 32 + 12 + len(SEALED["call"]["plaintext"].encode("utf-8")) + 16
    # Sealing hides; alone it proves no origin. The document says what does: the door's signature.
    assert "proves no origin" in described["origin"] and "`x-call-signature`" in described["origin"]


def test_a_call_is_sealed_to_the_instance_and_its_answer_to_the_key_inside_the_call() -> None:
    """ADR 0148 §5: the key an answer is sealed to never travels in a field a proxy could rewrite."""
    call = json.loads(SEALED["call"]["plaintext"])
    answer = json.loads(SEALED["answer"]["plaintext"])
    enrolment = json.loads((EXAMPLES / "outpost-enrolment-request.example.json").read_text(encoding="utf-8"))
    assert SEALED["call"]["recipient_key"] == enrolment["sealing_key"]
    assert SEALED["answer"]["recipient_key"] == call["answer_key"]
    assert (
        SEALED["call"]["associated_data"] == SEALED["answer"]["associated_data"] == call["id"] == answer["id"]
    )
    assert call == json.loads((EXAMPLES / "outpost-call.example.json").read_text(encoding="utf-8"))
    assert answer == json.loads((EXAMPLES / "outpost-answer.example.json").read_text(encoding="utf-8"))
    outpost.OutpostCall.model_validate(call)
    outpost.OutpostAnswer.model_validate(answer)
    # In clear, a poll's answer shows a call's id and its size, and nothing of what it asks.
    poll = json.loads((EXAMPLES / "outpost-poll-answer.example.json").read_text(encoding="utf-8"))
    handed = poll["calls"][0]
    assert set(handed) == {"id", "sealed", "signature"} and handed["id"] == call["id"]
    assert handed["sealed"] == SEALED["call"]["sealed"]
    for hidden in (call["tool"], call["endpoint"], call["answer_key"], "Invoice"):
        assert hidden not in json.dumps(poll)
    sent = json.loads((EXAMPLES / "outpost-sealed-answer.example.json").read_text(encoding="utf-8"))
    assert sent == {"sealed": SEALED["answer"]["sealed"]}
    answered = next(r for r in SIGNED["requests"] if r["name"] == "answer")
    assert json.loads(answered["body"]) == sent and answered["url"].endswith(f"/calls/{call['id']}/answer")


# ───────────────────────── a call is signed by the door (ADR 0148 §13) ─────────────────────────


def _door_key() -> Ed25519PrivateKey:
    """The vector's door key: like the others, the SHA-256 of a label written in the vector."""
    return Ed25519PrivateKey.from_private_bytes(_seed(SEALED["door"]["signing_key_label"]))


def _policy_example() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(
        (EXAMPLES / "outpost-policy.example.yaml").read_text(encoding="utf-8")
    )
    return loaded


def _handed() -> dict[str, Any]:
    """The call the poll's example hands over: its id, its envelope, the door's signature."""
    poll = json.loads((EXAMPLES / "outpost-poll-answer.example.json").read_text(encoding="utf-8"))
    handed: dict[str, Any] = poll["calls"][0]
    return handed


def _verified_call(handed: dict[str, Any], *, pinned: list[str], instance: str) -> bool:
    """What `x-call-signature.checks` asks of a daemon before it opens an envelope, in its order, written
    from the document. True when the envelope may be opened; a False is one refusal, `signature_refused`,
    whichever check failed."""
    signature = handed.get("signature")
    if not isinstance(signature, str):
        return False
    try:
        header = jwt.get_unverified_header(signature)
    except jwt.InvalidTokenError:
        return False
    key = header.get("kid")
    if header != outpost.call_signature_header(str(key)) or key not in pinned:
        return False
    try:
        claims = jwt.decode(
            signature,
            Ed25519PublicKey.from_public_bytes(_unb64(key)),
            algorithms=[DOOR["x-call-signature"]["algorithm"]],
        )
    except jwt.InvalidTokenError:
        return False
    expected = outpost.call_signature_claims(
        call_id=handed["id"], instance_id=instance, sealed=handed["sealed"]
    )
    return bool(claims == expected)


def _signed_by(key: Ed25519PrivateKey, handed: dict[str, Any], *, instance: str, **changed: Any) -> str:
    """A call's signature made with `key`; `changed` alters its header (`typ`, `kid`) or adds claims."""
    public = _b64(key.public_key().public_bytes(*RAW))
    header = outpost.call_signature_header(changed.pop("kid", public)) | (
        {"typ": changed.pop("typ")} if "typ" in changed else {}
    )
    claims = outpost.call_signature_claims(
        call_id=handed["id"], instance_id=instance, sealed=handed["sealed"]
    )
    return jwt.encode(claims | changed, key, algorithm=outpost.SIGNATURE_ALGORITHM, headers=header)


def test_the_policy_example_pins_the_key_that_signs_the_vectors_call() -> None:
    public = _b64(_door_key().public_key().public_bytes(*RAW))
    assert public == SEALED["door"]["signing_key"]
    assert _policy_example()["door_keys"] == [public]
    assert re.fullmatch(outpost.PUBLIC_KEY_PATTERN, public)
    # The door's key is none of the instance's: three keys, three uses.
    enrolment = json.loads((EXAMPLES / "outpost-enrolment-request.example.json").read_text(encoding="utf-8"))
    assert len({public, enrolment["signing_key"], enrolment["sealing_key"]}) == 3


def test_the_vectors_call_is_signed_by_the_door_and_binds_what_the_contract_says() -> None:
    handed, vector = _handed(), SEALED["call"]
    assert handed == {
        "id": vector["associated_data"],
        "sealed": vector["sealed"],
        "signature": vector["signature"],
    }
    assert re.fullmatch(outpost.CALL_SIGNATURE_PATTERN, handed["signature"])
    header = jwt.get_unverified_header(handed["signature"])
    claims = jwt.decode(handed["signature"], _door_key().public_key(), algorithms=["EdDSA"])
    assert (
        header == vector["signature_header"] == outpost.call_signature_header(SEALED["door"]["signing_key"])
    )
    assert (
        header["typ"] == DOOR["x-call-signature"]["type"] and header["kid"] == SEALED["door"]["signing_key"]
    )
    # The claims, and nothing else: this call, for this instance, in this envelope. No `iat`.
    assert list(claims) == DOOR["x-call-signature"]["claim_names"]
    assert claims == vector["signature_claims"]
    assert claims == {
        "call": handed["id"],
        "instance": SIGNED["instance_id"],
        "sealed_sha256": hashlib.sha256(handed["sealed"].encode("ascii")).hexdigest(),
    }
    assert vector["instance"] == SIGNED["instance_id"]
    # An outpost that pins the example's key makes this call, and only then opens its envelope.
    pinned = _policy_example()["door_keys"]
    assert _verified_call(handed, pinned=pinned, instance=SIGNED["instance_id"])
    opened = sealing.open_sealed(_recipient(vector), handed["id"], handed["sealed"])
    assert opened["id"] == handed["id"]
    # The deadline that makes the call fresh is inside what the signature covers.
    assert "deadline" in opened and opened["deadline"] in vector["plaintext"]
    # The installed library makes this very signature: no new dependency.
    again = _signed_by(_door_key(), handed, instance=SIGNED["instance_id"])
    assert jwt.get_unverified_header(again) == header
    assert jwt.decode(again, _door_key().public_key(), algorithms=["EdDSA"]) == claims


def _altered(text: str) -> str:
    """`text` with one character changed, still in the same alphabet."""
    return text[:-1] + ("A" if text[-1] != "A" else "B")


def test_a_call_that_is_not_the_one_the_door_signed_is_not_opened() -> None:
    """Each of the daemon's checks, failing alone. The refusal is the same for all: `signature_refused`."""
    handed, instance = _handed(), SIGNED["instance_id"]
    pinned = _policy_example()["door_keys"]
    assert _verified_call(handed, pinned=pinned, instance=instance)
    refused = {
        "a changed envelope": handed | {"sealed": _altered(handed["sealed"])},
        "another call's id": handed | {"id": "0d1c2b3a-another-call"},
        "no signature": {key: value for key, value in handed.items() if key != "signature"},
        "a null signature": handed | {"signature": None},
        "a signature that is no JWS": handed | {"signature": "garbage"},
        "a changed signature": handed | {"signature": _altered(handed["signature"])},
        "the instance's own request signature": handed | {"signature": SIGNED["requests"][1]["signature"]},
    }
    for what, call in refused.items():
        assert not _verified_call(call, pinned=pinned, instance=instance), what
    # The same call handed to another instance is not that instance's.
    assert not _verified_call(handed, pinned=pinned, instance="another-instance")
    # A `kid` the policy file does not pin: nothing is verified under it.
    assert not _verified_call(handed, pinned=[], instance=instance)
    other = Ed25519PrivateKey.generate()
    other_public = _b64(other.public_key().public_bytes(*RAW))
    assert not _verified_call(handed, pinned=[other_public], instance=instance)
    # A pinned set of several keys verifies under the one the signature names (a rotation under way).
    assert _verified_call(handed, pinned=[other_public, *pinned], instance=instance)


def test_a_signature_the_door_did_not_make_this_way_is_refused_even_under_its_key() -> None:
    """The header is the three members and the claims the three claims, and nothing else."""
    handed, instance = _handed(), SIGNED["instance_id"]
    pinned, door = _policy_example()["door_keys"], _door_key()
    forger = Ed25519PrivateKey.generate()
    forged = {
        # Whoever sits on the path signs with a key of its own, under its own name or under the door's.
        "a key nobody pinned": _signed_by(forger, handed, instance=instance),
        "a key nobody pinned, under the pinned name": _signed_by(
            forger, handed, instance=instance, kid=pinned[0]
        ),
        # The door's own key, used for something that is not a call's signature of this contract.
        "the request signature's type": _signed_by(
            door, handed, instance=instance, typ=outpost.REQUEST_SIGNATURE_TYPE
        ),
        "the default type": _signed_by(door, handed, instance=instance, typ="JWT"),
        "one claim more": _signed_by(door, handed, instance=instance, iat=1700000000),
        "one claim more, a deadline": _signed_by(
            door, handed, instance=instance, deadline="2099-01-01T00:00:00Z"
        ),
    }
    for what, signature in forged.items():
        assert not _verified_call(handed | {"signature": signature}, pinned=pinned, instance=instance), what
    unsigned = jwt.encode(
        outpost.call_signature_claims(call_id=handed["id"], instance_id=instance, sealed=handed["sealed"]),
        None,
        algorithm="none",
        headers={"typ": outpost.CALL_SIGNATURE_TYPE, "kid": pinned[0]},
    )
    assert not _verified_call(handed | {"signature": unsigned}, pinned=pinned, instance=instance)
    # And the same helper accepts what the door does make: the refusals above are not the helper's doing.
    made = _signed_by(door, handed, instance=instance)
    assert _verified_call(handed | {"signature": made}, pinned=pinned, instance=instance)


def test_a_call_sealed_by_someone_else_opens_and_is_refused_all_the_same() -> None:
    """The gap the signature closes. Whoever terminates the daemon's TLS knows the instance's public
    sealing key (it crossed at enrolment): it can seal a call of its own, with an `answer_key` of its own,
    and that envelope opens. Sealing proves no origin; an outpost therefore opens nothing it has not
    verified under a pinned key."""
    enrolment = json.loads((EXAMPLES / "outpost-enrolment-request.example.json").read_text(encoding="utf-8"))
    instance, pinned = SIGNED["instance_id"], _policy_example()["door_keys"]
    proxy = X25519PrivateKey.generate()
    forged_call = json.loads(SEALED["call"]["plaintext"]) | {
        "id": "forged-0001",
        "tool": "delete_project",
        "answer_key": _b64(proxy.public_key().public_bytes(*RAW)),
    }
    envelope = sealing.seal(enrolment["sealing_key"], "forged-0001", forged_call)
    # It opens: the instance's key is all a sealer needs…
    assert sealing.open_sealed(_recipient(SEALED["call"]), "forged-0001", envelope) == forged_call
    outpost.OutpostCall.model_validate(forged_call)
    # …and it is refused, unsigned, self-signed, or wearing the signature of a call the door did sign.
    forger = Ed25519PrivateKey.generate()
    forged = {"id": "forged-0001", "sealed": envelope}
    attempts = [
        forged,
        forged | {"signature": _signed_by(forger, forged, instance=instance)},
        forged | {"signature": _signed_by(forger, forged, instance=instance, kid=pinned[0])},
        forged | {"signature": _handed()["signature"]},
        forged | {"id": _handed()["id"], "signature": _handed()["signature"]},
    ]
    for attempt in attempts:
        assert not _verified_call(attempt, pinned=pinned, instance=instance), attempt.get(
            "signature", "unsigned"
        )
    # The envelope of a real call under the real signature is the one thing that passes.
    assert _verified_call(_handed(), pinned=pinned, instance=instance)


def test_nothing_else_of_an_answer_is_signed_and_the_document_says_so_in_one_place() -> None:
    poll = json.loads((EXAMPLES / "outpost-poll-answer.example.json").read_text(encoding="utf-8"))
    signed_fields = [name for name, value in poll.items() if "signature" in json.dumps(value)]
    assert signed_fields == ["calls"]
    said = [name for name, section in DOOR.items() if name.startswith("x-") and "not_signed" in section]
    assert said == ["x-call-signature"]
    schemas = DOOR["components"]["schemas"]
    assert "signature" not in schemas["PollAnswer"]["properties"]
    assert "signature" not in schemas["DoorProblem"]["properties"]
    answer = json.loads((CONTRACTS / "schemas" / "outpost-enrolment-answer.schema.json").read_text("utf-8"))
    assert "signature" not in answer["properties"]


# ───────────────────────── where the door meets the platform's own shapes ─────────────────────────


def test_the_digest_of_arguments_is_the_one_the_platform_keeps() -> None:
    """The outpost's audit line and the `tool.called` event of the same call can be compared."""
    from choregos_api.routers.internal import json_sha256

    for arguments in (
        {},
        {"title": "Δ of 3 € on 請求書", "labels": ["bug", "prod"]},
        {"b": [1, {"z": None}], "a": 1.5},
    ):
        assert outpost.arguments_sha256(arguments) == json_sha256(arguments)


def test_a_problem_the_api_builds_is_a_problem_of_the_door() -> None:
    """The API always adds `instance` to a problem; with a `code` and its clock it is the door's."""
    from choregos_api.errors import ApiError

    for code, status in outpost.DOOR_STATUS.items():
        extra: dict[str, Any] = {"code": code.value, "server_time": "2026-11-12T10:07:31Z"}
        if code is outpost.OutpostDoorCode.PROTOCOL_UNSUPPORTED:
            extra["protocol"] = {"current": 2, "minimum": 2}
        body = ApiError(status, "Refused", "why", retry_after=5, extra=extra).to_problem("/outposts/v1/poll")
        schema = {"$ref": "#/components/schemas/DoorProblem"}
        assert not list(_validator(schema).iter_errors(body)), code
        read = outpost.OutpostDoorProblem.model_validate(body)
        assert read.known_code is code and read.status == status
    bare = ApiError(401, "Not authenticated").to_problem("/outposts/v1/poll")
    assert list(_validator({"$ref": "#/components/schemas/DoorProblem"}).iter_errors(bare))


def test_the_records_name_this_contract() -> None:
    door = (
        ROOT / "docs" / "adr" / "0148-the-outpost-door-is-a-third-door-with-its-own-contract.md"
    ).read_text(encoding="utf-8")
    assert "packages/contracts/outpost-door.yaml" in door
    for verb, path in sorted(VERBS):
        assert f"`{verb.upper()} {path}`" in door, path
    for adr in ("ADR 0147", "ADR 0148"):
        assert adr in DOOR["info"]["description"]
    # The call's signature is a decision of the record (§13), and the policy example of ADR 0147 pins a key.
    assert "13. **A call is signed by the door" in door
    for name in ("`door_keys`", "`signature_refused`", "`choregos-outpost-call+jwt`", "`sealed_sha256`"):
        assert name in door, name
    outposts = (ROOT / "docs" / "adr" / "0147-an-outpost-only-dials-out.md").read_text(encoding="utf-8")
    assert "ADR 0148 §13" in outposts and f'door_keys: ["{SEALED["door"]["signing_key"]}"]' in outposts


def _decision(record: str, number: int) -> str:
    """One numbered decision of a record, as one line of text."""
    text = (ROOT / "docs" / "adr" / record).read_text(encoding="utf-8")
    start = text.index(f"\n{number}. **")
    end = text.find(f"\n{number + 1}. **", start)
    return " ".join(text[start : end if end != -1 else None].split())


def test_the_records_say_what_building_against_the_contract_found() -> None:
    """S65-10b: the two implementations met the records where they were silent or said too much."""
    door = "0148-the-outpost-door-is-a-third-door-with-its-own-contract.md"
    # ADR 0148 §6: the door reads one organisation in one statement, then never works under all of them.
    scope = _decision(door, 6)
    assert "The door never works under the all-organisations scope" in scope
    assert "never sets the all-organisations scope" not in scope
    assert "in one statement under the all-organisations scope" in scope
    assert "names the outpost first" in scope and "carry the outpost's id" in scope
    assert "narrows to that organisation before anything else runs" in scope
    assert "`outposts` is the organisation's registry" in scope
    assert "listed under the organisation scope" in scope and "one row once an outpost is named" in scope
    for table in ("`outpost_instances`", "`outpost_enrolments`", "`outpost_calls`"):
        assert table in scope, table
    assert scope.index("`outpost_calls`") < scope.index("require the outpost setting")
    assert "answers 404, never 403" in scope  # what the decision already said still stands
    # ADR 0147 §5: a revoked key does not come back for as long as its outpost exists.
    revocation = _decision("0147-an-outpost-only-dials-out.md", 5)
    assert "A revoked key does not come back: that holds for as long as its outpost exists" in revocation
    assert "deletes its instances, and with them the memory of their keys" in revocation
    assert "a new enrolment like any other" in revocation and "approval against its fingerprint" in revocation
    assert "for as long as its outpost exists (ADR 0147 §5)" in _decision(door, 3)
    # ADR 0148 §13: the journal decides first, and a lapsed id is refused without being verified.
    signed = _decision(door, 13)
    assert "The journal decides first, always" in signed
    assert "refused without being verified" in signed
    # The decisions kept their numbers: other pages cite §5, §8, §12 and §13.
    assert _decision(door, 5).startswith("5. **Requests and answers are sealed end to end.**")
    assert _decision(door, 8).startswith("8. **Its own Deployment and pool")
    assert _decision(door, 12).startswith("12. **Why this door does not make Choregos a gateway**")
    assert _decision(door, 13).startswith("13. **A call is signed by the door.**")
