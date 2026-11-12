# SPDX-License-Identifier: Apache-2.0
"""Outposts (S65-10, ADRs 0147-0149): the schemas, the models, the door's document and the management
API say the same thing.

Contract only: no route serves any of this yet (`CONTRACT_ONLY` in
`apps/api/tests/test_contract_conformance.py`), so what is proven here is what the documents say, and
that the three forms a daemon and a door will read (JSON Schema, Python model, the door's own
document) cannot drift apart. `tests/docs/test_outpost_door_contract.py` holds what needs the
cryptography: the two signatures (a request's, the instance's; a call's, the door's) and the sealed
envelope.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import choregos_contracts as contracts
import pytest
import yaml
from choregos_contracts import outpost
from choregos_contracts.enums import (
    OutpostAnswerOutcome,
    OutpostAuditEvent,
    OutpostCallKind,
    OutpostCallState,
    OutpostCapability,
    OutpostDoorCode,
    OutpostInstanceStatus,
    OutpostRefusalCode,
    OutpostStatus,
)
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaError
from jsonschema.exceptions import best_match
from pydantic import BaseModel, ValidationError

from .test_contracts import _compare

EXAMPLES = contracts.schemas_dir() / "examples"
REPOSITORY = Path(__file__).resolve().parents[3]
DOOR = contracts.load_outpost_door()
DOOR_SCHEMAS: dict[str, Any] = DOOR["components"]["schemas"]
OPENAPI = contracts.load_openapi()
API_SCHEMAS: dict[str, Any] = OPENAPI["components"]["schemas"]
VERBS = {"get", "post", "put", "patch", "delete"}


def _json(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _policy() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((EXAMPLES / "outpost-policy.example.yaml").read_text("utf-8"))
    return loaded


#: The door's public key of the sealing vector: test material, derived from a label written there.
PINNED: str = _json("outpost-sealing.vector.json")["door"]["signing_key"]


def _other_keys(count: int) -> list[str]:
    """Strings shaped like keys, all different from the pinned one and from each other."""
    return [letter + PINNED[1:] for letter in "ABCDEFGH"[:count]]


def _file(schema: str) -> Draft202012Validator:
    return Draft202012Validator(
        contracts.load_schema(schema), format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def _door(name: str) -> Draft202012Validator:
    return Draft202012Validator(
        {"components": DOOR["components"], **DOOR_SCHEMAS[name]},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def _api(name: str) -> Draft202012Validator:
    return Draft202012Validator({"components": OPENAPI["components"], **API_SCHEMAS[name]})


def _accepts(model: type[BaseModel], document: Any) -> bool:
    try:
        model.model_validate(document)
    except ValidationError:
        return False
    return True


# ───────────────────────── every example, against its schema and its model ─────────────────────────

#: (example, schema file, model). A `.jsonl` example is read line by line.
FILE_EXAMPLES: list[tuple[str, str, type[BaseModel]]] = [
    ("outpost-call.example.json", "outpost-call.schema.json", outpost.OutpostCall),
    ("outpost-call.list-tools.example.json", "outpost-call.schema.json", outpost.OutpostCall),
    ("outpost-answer.example.json", "outpost-answer.schema.json", outpost.OutpostAnswer),
    ("outpost-answer.list-tools.example.json", "outpost-answer.schema.json", outpost.OutpostAnswer),
    ("outpost-answer.unanswered.example.json", "outpost-answer.schema.json", outpost.OutpostAnswer),
    ("outpost-answer.error.example.json", "outpost-answer.schema.json", outpost.OutpostAnswer),
    ("outpost-refusal.example.json", "outpost-refusal.schema.json", outpost.OutpostRefusal),
    ("outpost-audit-line.example.json", "outpost-audit-line.schema.json", outpost.OutpostAuditLine),
    ("outpost-audit-log.example.jsonl", "outpost-audit-line.schema.json", outpost.OutpostAuditLine),
    (
        "outpost-enrolment-request.example.json",
        "outpost-enrolment-request.schema.json",
        outpost.OutpostEnrolmentRequest,
    ),
    (
        "outpost-enrolment-answer.example.json",
        "outpost-enrolment-answer.schema.json",
        outpost.OutpostEnrolmentAnswer,
    ),
]

#: (example, component of the door's document, model).
DOOR_EXAMPLES: list[tuple[str, str, type[BaseModel]]] = [
    ("outpost-poll-request.example.json", "PollRequest", outpost.OutpostPollRequest),
    ("outpost-poll-answer.example.json", "PollAnswer", outpost.OutpostPollAnswer),
    ("outpost-sealed-answer.example.json", "SealedAnswer", outpost.OutpostSealedAnswer),
    ("outpost-call-ack.example.json", "CallAck", outpost.OutpostCallAck),
    ("outpost-door-problem.example.json", "DoorProblem", outpost.OutpostDoorProblem),
    ("outpost-door-problem.protocol.example.json", "DoorProblem", outpost.OutpostDoorProblem),
]


def _documents(example: str) -> list[Any]:
    if example.endswith(".jsonl"):
        lines = (EXAMPLES / example).read_text(encoding="utf-8").splitlines()
        assert len(lines) > 1, "a log of one line shows nothing a single line does not"
        return [json.loads(line) for line in lines]
    return [_json(example)]


def _keeps_every_key(model: type[BaseModel], document: dict[str, Any]) -> None:
    """A model that validates an example and drops one of its keys has drifted from the schema."""
    dumped = model.model_validate(document).model_dump(mode="json", exclude_none=True)
    lost = {key for key, value in document.items() if value is not None} - set(dumped)
    assert not lost, f"keys lost by {model.__name__}: {sorted(lost)}"


@pytest.mark.parametrize(("example", "schema", "model"), FILE_EXAMPLES)
def test_an_example_is_valid_for_its_schema_and_its_model(
    example: str, schema: str, model: type[BaseModel]
) -> None:
    for document in _documents(example):
        errors = sorted(_file(schema).iter_errors(document), key=lambda e: list(e.path))
        assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)
        _keeps_every_key(model, document)


@pytest.mark.parametrize(("example", "component", "model"), DOOR_EXAMPLES)
def test_an_example_of_the_door_is_valid_for_its_component_and_its_model(
    example: str, component: str, model: type[BaseModel]
) -> None:
    document = _json(example)
    errors = sorted(_door(component).iter_errors(document), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)
    _keeps_every_key(model, document)


def test_no_outpost_example_is_left_untested() -> None:
    """An example nobody validates says nothing; the two vectors are read by `tests/docs`."""
    tested = {name for name, _, _ in FILE_EXAMPLES + DOOR_EXAMPLES} | {"outpost-policy.example.yaml"}
    vectors = {"outpost-signature.vector.json", "outpost-sealing.vector.json"}
    on_disk = {path.name for path in EXAMPLES.glob("outpost-*")}
    assert on_disk == tested | vectors


def test_the_outpost_schemas_are_declared_and_shipped() -> None:
    declared = {name for name in contracts.SCHEMA_FILES if name.startswith("outpost-")}
    assert declared == {schema for _, schema, _ in FILE_EXAMPLES} | {"outpost-policy.schema.json"}
    assert contracts.outpost_door_path().name == "outpost-door.yaml"
    assert contracts.outpost_door_path().parent == contracts.openapi_path().parent
    pyproject = (contracts.contracts_dir() / "pyproject.toml").read_text(encoding="utf-8")
    assert '"outpost-door.yaml" = "choregos_contracts/outpost-door.yaml"' in pyproject, (
        "the door's document must be in the wheel: the daemon reads the contract it was built against"
    )


# ───────────────────────── the policy file ─────────────────────────


def test_the_policy_example_is_the_one_of_adr_0147_byte_for_byte() -> None:
    """The record shows operators one file; the contract must be about that file and no other."""
    record = (REPOSITORY / "docs" / "adr" / "0147-an-outpost-only-dials-out.md").read_text(encoding="utf-8")
    blocks = re.findall(r"^   ```yaml\n(.*?)^   ```$", record, re.S | re.M)
    assert len(blocks) == 1, "ADR 0147 no longer holds exactly one policy example"
    shown = "".join(line.removeprefix("   ") for line in blocks[0].splitlines(keepends=True))
    assert shown == (EXAMPLES / "outpost-policy.example.yaml").read_text(encoding="utf-8")


def test_the_policy_example_is_valid_and_reads_as_the_record_says() -> None:
    document = _policy()
    assert not list(_file("outpost-policy.schema.json").iter_errors(document))
    policy = outpost.OutpostPolicy.model_validate(document)
    assert policy.control_plane == "https://outposts.example.com"
    assert policy.door_keys == [PINNED]  # the key that signs the vector's call: the example verifies it
    assert policy.concurrency == 8
    assert policy.audit.file == "/var/log/choregos-outpost/audit.jsonl"
    tracker = policy.endpoints["tracker"]
    assert tracker.url == "http://tracker-mcp.tools.svc:8080/mcp"
    assert tracker.auth is not None
    assert (tracker.auth.header, tracker.auth.from_file) == ("Authorization", "/secrets/tracker-token")
    assert tracker.tools == ["search_*", "get_*", "create_issue"]
    assert (tracker.writes, tracker.timeout_s, tracker.max_payload_kb) == ("allow", 60, 512)


def test_a_policy_denies_writes_and_bounds_itself_unless_it_says_otherwise() -> None:
    document = {
        "control_plane": "https://outposts.example.com",
        "door_keys": [PINNED],
        "endpoints": {"tracker": {"url": "https://tracker.internal/mcp", "tools": []}},
        "audit": {"file": "/var/log/audit.jsonl"},
    }
    assert not list(_file("outpost-policy.schema.json").iter_errors(document))
    policy = outpost.OutpostPolicy.model_validate(document)
    endpoint = policy.endpoints["tracker"]
    assert endpoint.writes == "deny"  # ADR 0147 §6: `deny` by default
    assert endpoint.auth is None
    assert (endpoint.timeout_s, endpoint.max_payload_kb, policy.concurrency) == (30, 256, 4)
    declared = contracts.load_schema("outpost-policy.schema.json")
    fields = declared["$defs"]["endpoint"]["properties"]
    assert fields["writes"]["default"] == "deny"
    assert (fields["timeout_s"]["default"], fields["max_payload_kb"]["default"]) == (30, 256)
    assert declared["properties"]["concurrency"]["default"] == 4


def _changed(path: tuple[str | int, ...], value: Any) -> dict[str, Any]:
    """The record's policy with one field replaced; `...` removes it."""
    document = copy.deepcopy(_policy())
    node: Any = document
    for step in path[:-1]:
        node = node[step]
    if value is ...:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return document


TRACKER = ("endpoints", "tracker")
#: One document per rule of the policy file: (what is wrong, where, the document).
BROKEN_POLICIES: list[tuple[str, tuple[str | int, ...], dict[str, Any]]] = [
    ("no control plane", ("control_plane",), _changed(("control_plane",), ...)),
    (
        "a control plane with a path",
        ("control_plane",),
        _changed(("control_plane",), "https://outposts.example.com/door"),
    ),
    (
        "a control plane with credentials",
        ("control_plane",),
        _changed(("control_plane",), "https://user:pass@outposts.example.com"),
    ),
    ("a control plane that is not http", ("control_plane",), _changed(("control_plane",), "wss://outposts")),
    ("a key the file does not have", ("relay",), _changed(("relay",), {"to": "10.0.0.0/8"})),
    ("no pinned door key", ("door_keys",), _changed(("door_keys",), ...)),
    ("an empty list of door keys", ("door_keys",), _changed(("door_keys",), [])),
    (
        "more door keys than a rotation needs",
        ("door_keys",),
        _changed(("door_keys",), [PINNED, *_other_keys(outpost.MAX_DOOR_KEYS)]),
    ),
    ("a door key pinned twice", ("door_keys",), _changed(("door_keys",), [PINNED, PINNED])),
    ("a door key that is not a key", ("door_keys", 0), _changed(("door_keys",), ["not-a-key"])),
    (
        "a door key spelt a second way",
        ("door_keys", 1),
        _changed(("door_keys",), [PINNED, PINNED[:-1] + "x"]),
    ),
    (
        "a fingerprint where a key is pinned",
        ("door_keys", 0),
        _changed(("door_keys",), [outpost.fingerprint_of(PINNED)]),
    ),
    ("no endpoints", ("endpoints",), _changed(("endpoints",), ...)),
    (
        "an alias that is not a name",
        ("endpoints", "Tracker/1"),
        _changed(("endpoints", "Tracker/1"), _policy()["endpoints"]["tracker"]),
    ),
    ("an endpoint without a URL", (*TRACKER, "url"), _changed((*TRACKER, "url"), ...)),
    (
        "a URL with a query",
        (*TRACKER, "url"),
        _changed((*TRACKER, "url"), "http://tracker-mcp.tools.svc:8080/mcp?key=1"),
    ),
    (
        "a URL with credentials",
        (*TRACKER, "url"),
        _changed((*TRACKER, "url"), "http://user:pass@tracker-mcp.tools.svc:8080/mcp"),
    ),
    ("an endpoint that names no tool list", (*TRACKER, "tools"), _changed((*TRACKER, "tools"), ...)),
    ("a tool pattern that is a regex", (*TRACKER, "tools", 1), _changed((*TRACKER, "tools", 1), "get_.+")),
    (
        "writes that are neither denied nor allowed",
        (*TRACKER, "writes"),
        _changed((*TRACKER, "writes"), "ask"),
    ),
    ("a timeout of zero", (*TRACKER, "timeout_s"), _changed((*TRACKER, "timeout_s"), 0)),
    (
        "a timeout beyond the longest lease",
        (*TRACKER, "timeout_s"),
        _changed((*TRACKER, "timeout_s"), outpost.MAX_LEASE_S + 1),
    ),
    (
        "a payload ceiling above 1 MiB",
        (*TRACKER, "max_payload_kb"),
        _changed((*TRACKER, "max_payload_kb"), 1025),
    ),
    ("a payload ceiling of zero", (*TRACKER, "max_payload_kb"), _changed((*TRACKER, "max_payload_kb"), 0)),
    (
        "a credential that names no file",
        (*TRACKER, "auth", "from_file"),
        _changed((*TRACKER, "auth"), {"header": "Authorization"}),
    ),
    (
        "a credential file that is not an absolute path",
        (*TRACKER, "auth", "from_file"),
        _changed((*TRACKER, "auth", "from_file"), "secrets/tracker-token"),
    ),
    (
        "a credential written in the file itself",
        (*TRACKER, "auth", "value"),
        _changed((*TRACKER, "auth", "value"), "not-a-credential"),
    ),
    (
        "a header that is not a header name",
        (*TRACKER, "auth", "header"),
        _changed((*TRACKER, "auth", "header"), "X Key"),
    ),
    (
        "a rule on a name the control plane asserts",
        (*TRACKER, "projects"),
        _changed((*TRACKER, "projects"), ["billing-api"]),
    ),
    ("no concurrency at all", ("concurrency",), _changed(("concurrency",), 0)),
    (
        "more concurrency than an instance may hold",
        ("concurrency",),
        _changed(("concurrency",), outpost.MAX_CONCURRENCY + 1),
    ),
    ("no audit log", ("audit",), _changed(("audit",), ...)),
    ("an audit log that names no file", ("audit", "file"), _changed(("audit",), {})),
    (
        "an audit file that is not an absolute path",
        ("audit", "file"),
        _changed(("audit", "file"), "audit.jsonl"),
    ),
]


def _schema_path(error: SchemaError) -> tuple[str | int, ...]:
    """Where a schema error is, down to the field: a missing or unknown key is named in the message, and a
    refused key is the instance of an error raised under `propertyNames`."""
    path: tuple[str | int, ...] = tuple(error.absolute_path)
    if "propertyNames" in error.absolute_schema_path:
        return (*path, error.instance)
    if error.validator in {"required", "additionalProperties"}:
        named = re.findall(r"'([^']+)'", error.message)
        assert named, error.message
        return (*path, named[0])
    return path


def _model_path(error: ValidationError) -> tuple[str | int, ...]:
    """Where the model's first error is; pydantic marks a refused dictionary key with `[key]`."""
    return tuple(step for step in error.errors()[0]["loc"] if step != "[key]")


@pytest.mark.parametrize(("what", "where", "document"), BROKEN_POLICIES, ids=[b[0] for b in BROKEN_POLICIES])
def test_a_broken_policy_is_refused_with_the_path_of_its_field(
    what: str, where: tuple[str | int, ...], document: dict[str, Any]
) -> None:
    error = best_match(_file("outpost-policy.schema.json").iter_errors(document))
    assert error is not None, f"the schema accepts {what}"
    assert _schema_path(error) == where
    with pytest.raises(ValidationError) as refused:
        outpost.OutpostPolicy.model_validate(document)
    assert _model_path(refused.value) == where, what


def test_the_broken_policies_cover_every_field_of_the_file() -> None:
    """A field added to the policy file comes with the document that breaks it."""
    schema = contracts.load_schema("outpost-policy.schema.json")
    endpoint = schema["$defs"]["endpoint"]["properties"]
    fields = (
        {(name,) for name in schema["properties"]}
        | {(*TRACKER, name) for name in endpoint}
        | {(*TRACKER, "auth", name) for name in endpoint["auth"]["properties"]}
        | {("audit", name) for name in schema["$defs"]["audit"]["properties"]}
    )
    broken = {tuple(step for step in where if not isinstance(step, int)) for _, where, _ in BROKEN_POLICIES}
    assert fields - {(*TRACKER, "auth")} <= broken, sorted(fields - broken)


# ───────────────────────── schema and model declare the same fields ─────────────────────────

SAME_FIELDS: list[tuple[str, dict[str, Any], type[BaseModel]]] = [
    ("policy", contracts.load_schema("outpost-policy.schema.json"), outpost.OutpostPolicy),
    (
        "policy.endpoint",
        contracts.load_schema("outpost-policy.schema.json")["$defs"]["endpoint"],
        outpost.OutpostEndpoint,
    ),
    (
        "policy.endpoint.auth",
        contracts.load_schema("outpost-policy.schema.json")["$defs"]["endpoint"]["properties"]["auth"],
        outpost.OutpostEndpointAuth,
    ),
    (
        "policy.audit",
        contracts.load_schema("outpost-policy.schema.json")["$defs"]["audit"],
        outpost.OutpostAuditTarget,
    ),
    ("call", contracts.load_schema("outpost-call.schema.json"), outpost.OutpostCall),
    (
        "call.asserted",
        contracts.load_schema("outpost-call.schema.json")["$defs"]["asserted"],
        outpost.OutpostAssertedNames,
    ),
    ("answer", contracts.load_schema("outpost-answer.schema.json"), outpost.OutpostAnswer),
    (
        "answer.tool",
        contracts.load_schema("outpost-answer.schema.json")["$defs"]["tool"],
        outpost.OutpostTool,
    ),
    (
        "answer.error",
        contracts.load_schema("outpost-answer.schema.json")["$defs"]["error"],
        outpost.OutpostServerError,
    ),
    ("refusal", contracts.load_schema("outpost-refusal.schema.json"), outpost.OutpostRefusal),
    ("audit-line", contracts.load_schema("outpost-audit-line.schema.json"), outpost.OutpostAuditLine),
    (
        "audit-line.asserted",
        contracts.load_schema("outpost-audit-line.schema.json")["$defs"]["asserted"],
        outpost.OutpostLoggedNames,
    ),
    (
        "enrolment-request",
        contracts.load_schema("outpost-enrolment-request.schema.json"),
        outpost.OutpostEnrolmentRequest,
    ),
    (
        "enrolment-answer",
        contracts.load_schema("outpost-enrolment-answer.schema.json"),
        outpost.OutpostEnrolmentAnswer,
    ),
    (
        "enrolment-answer.window",
        contracts.load_schema("outpost-enrolment-answer.schema.json")["$defs"]["window"],
        outpost.OutpostProtocolWindow,
    ),
    ("door.ProtocolWindow", DOOR_SCHEMAS["ProtocolWindow"], outpost.OutpostProtocolWindow),
    ("door.PollRequest", DOOR_SCHEMAS["PollRequest"], outpost.OutpostPollRequest),
    ("door.PollAnswer", DOOR_SCHEMAS["PollAnswer"], outpost.OutpostPollAnswer),
    ("door.SealedCall", DOOR_SCHEMAS["SealedCall"], outpost.OutpostSealedCall),
    ("door.SealedAnswer", DOOR_SCHEMAS["SealedAnswer"], outpost.OutpostSealedAnswer),
    ("door.CallAck", DOOR_SCHEMAS["CallAck"], outpost.OutpostCallAck),
    ("door.DoorProblem", DOOR_SCHEMAS["DoorProblem"], outpost.OutpostDoorProblem),
]


@pytest.mark.parametrize(("trail", "node", "model"), SAME_FIELDS, ids=[s[0] for s in SAME_FIELDS])
def test_a_schema_and_its_model_declare_the_same_fields(
    trail: str, node: dict[str, Any], model: type[BaseModel]
) -> None:
    gaps: list[str] = []
    _compare(trail, node, model, gaps)
    assert not gaps, "\n".join(gaps)
    assert node.get("properties"), f"{trail} declares no field: the comparison would prove nothing"


def test_every_outpost_model_is_compared_with_a_schema() -> None:
    models = {
        value
        for value in vars(outpost).values()
        if isinstance(value, type) and issubclass(value, BaseModel) and value.__module__ == outpost.__name__
    }
    assert models - {model for _, _, model in SAME_FIELDS} == {outpost.Lenient}


def test_what_a_daemon_sends_is_closed_and_what_it_reads_is_open() -> None:
    """ADR 0148 §9: a hostile outpost meets a closed shape. §3: a later need reaches an older daemon as a
    field it ignores."""
    sent = [
        contracts.load_schema("outpost-enrolment-request.schema.json"),
        contracts.load_schema("outpost-answer.schema.json"),
        contracts.load_schema("outpost-refusal.schema.json"),
        DOOR_SCHEMAS["PollRequest"],
        DOOR_SCHEMAS["SealedAnswer"],
    ]
    for schema in sent:
        assert schema["additionalProperties"] is False, schema.get("title")
    read = [
        contracts.load_schema("outpost-enrolment-answer.schema.json"),
        contracts.load_schema("outpost-call.schema.json"),
        DOOR_SCHEMAS["PollAnswer"],
        DOOR_SCHEMAS["SealedCall"],
        DOOR_SCHEMAS["CallAck"],
        DOOR_SCHEMAS["DoorProblem"],
    ]
    for schema in read:
        assert "additionalProperties" not in schema, schema.get("title")
    answer = _json("outpost-poll-answer.example.json") | {"a_later_lane": [{"id": "x"}]}
    assert not list(_door("PollAnswer").iter_errors(answer))
    assert "a_later_lane" not in outpost.OutpostPollAnswer.model_validate(answer).model_dump()
    request = _json("outpost-poll-request.example.json") | {"a_later_field": 1}
    assert list(_door("PollRequest").iter_errors(request))
    assert not _accepts(outpost.OutpostPollRequest, request)
    # The files the outpost writes or reads alone are closed too: a typo is not a silent default.
    for name in ("outpost-policy.schema.json", "outpost-audit-line.schema.json"):
        assert contracts.load_schema(name)["additionalProperties"] is False


# ───────────────────────── a call, an answer, an audit line: the conditions ─────────────────────────


def _both(schema: str, model: type[BaseModel], document: Any) -> tuple[bool, bool]:
    """(the schema accepts it, the model accepts it)."""
    return not list(_file(schema).iter_errors(document)), _accepts(model, document)


def _without(document: dict[str, Any], *names: str) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key not in names}


def test_a_call_is_read_the_same_by_the_schema_and_the_model() -> None:
    call, listing = _json("outpost-call.example.json"), _json("outpost-call.list-tools.example.json")
    accepted = [
        call,
        listing,
        _without(call, "asserted"),
        call | {"a_later_field": True},
        listing | {"tool": None, "arguments": None},
        call | {"asserted": call["asserted"] | {"a_later_name": "x"}},
    ]
    refused = [
        _without(call, "tool"),
        _without(call, "arguments"),
        _without(call, "endpoint"),
        _without(call, "deadline"),
        _without(call, "answer_key"),
        _without(call, "id"),
        _without(call, "kind"),
        call | {"tool": None},
        call | {"endpoint": "Tracker"},
        call | {"answer_key": "short"},
        call | {"id": "../other"},
        call | {"asserted": None},
        listing | {"tool": "search_issues"},
        listing | {"arguments": {}},
        _without(listing, "endpoint"),
    ]
    for document in accepted:
        assert _both("outpost-call.schema.json", outpost.OutpostCall, document) == (True, True), document
    for document in refused:
        assert _both("outpost-call.schema.json", outpost.OutpostCall, document) == (False, False), document


def test_a_call_says_nothing_of_whether_the_tool_writes() -> None:
    """ADR 0147 §6: the outpost reads that from its own server, never from the call."""
    declared = contracts.load_schema("outpost-call.schema.json")["properties"]
    assert not {"write", "writes", "read_only", "access"} & set(declared)
    assert not {"write", "writes", "read_only", "access"} & set(outpost.OutpostCall.model_fields)


def test_a_later_kind_is_readable_and_refused_by_name() -> None:
    """Room for later kinds without breaking protocol 1: a daemon can read the call far enough to refuse it
    with `unsupported_kind`, instead of failing on it."""
    later = {"id": "0f4b", "kind": "probe_endpoint", "whatever": {"it": "needs"}}
    assert _both("outpost-call.schema.json", outpost.OutpostCall, later) == (True, True)
    call = outpost.OutpostCall.model_validate(later)
    assert call.known_kind is None
    assert outpost.OutpostCall.model_validate(_json("outpost-call.example.json")).known_kind is (
        OutpostCallKind.CALL_TOOL
    )
    assert OutpostRefusalCode.UNSUPPORTED_KIND.value == "unsupported_kind"
    assert "unsupported_kind" in DOOR["x-protocol"]["later_kinds"]
    known = contracts.load_schema("outpost-call.schema.json")["$defs"]["known_kind"]["enum"]
    assert known == [kind.value for kind in OutpostCallKind] == ["call_tool", "list_tools"]


def test_an_answer_is_read_the_same_by_the_schema_and_the_model() -> None:
    result = _json("outpost-answer.example.json")
    tools = _json("outpost-answer.list-tools.example.json")
    unanswered = _json("outpost-answer.unanswered.example.json")
    error = _json("outpost-answer.error.example.json")
    oversized = {"id": result["id"], "outcome": "oversized", "write": True}
    accepted = [
        result,
        tools,
        unanswered,
        error,
        oversized,
        result | {"tools": None, "error": None},
        tools | {"write": None},
    ]
    # `write` is what the outpost read of the tool it called: a listing calls none (S65-10b).
    assert "write" not in tools
    for written in (True, False):
        assert _both("outpost-answer.schema.json", outpost.OutpostAnswer, tools | {"write": written}) == (
            False,
            False,
        )
    declared = contracts.load_schema("outpost-answer.schema.json")["properties"]["write"]["description"]
    assert "Null for a kind that calls no tool (`list_tools`)" in declared
    refused = [
        _without(result, "result"),
        result | {"tools": tools["tools"]},
        result | {"error": error["error"]},
        result | {"result": None},
        error | {"result": result["result"]},
        _without(error, "error"),
        unanswered | {"result": result["result"]},
        oversized | {"result": result["result"]},
        oversized | {"error": error["error"]},
        result | {"outcome": "done"},
        result | {"arguments": {}},
        result | {"duration_ms": -1},
        tools | {"tools": [{"description": "a tool with no name"}]},
        _without(result, "id"),
    ]
    for document in accepted:
        assert _both("outpost-answer.schema.json", outpost.OutpostAnswer, document) == (True, True), document
    for document in refused:
        assert _both("outpost-answer.schema.json", outpost.OutpostAnswer, document) == (False, False), (
            document
        )


def test_an_audit_line_is_read_the_same_by_the_schema_and_the_model() -> None:
    received, under_way, executed, _, refused_line, unsigned = _documents("outpost-audit-log.example.jsonl")
    unreadable = {
        "at": "2026-11-12T10:05:00Z",
        "event": "refused",
        "call_id": "4a1c",
        "signed_by": PINNED,
        "refusal": "call_unreadable",
    }
    replayed = executed | {"event": "replayed", "outcome": "unanswered"}
    assert unsigned["refusal"] == "signature_refused" and unsigned["signed_by"] is None
    # A copy of a call that is still under way: the journal holds its id and no answer to replay yet
    # (S65-10b). An `executed` line still says what was reported.
    assert under_way == {"at": under_way["at"], "event": "replayed", "call_id": received["call_id"]}
    assert received["at"] < under_way["at"] < executed["at"]
    accepted = [
        received,
        executed,
        refused_line,
        unreadable,
        replayed,
        under_way,
        _without(replayed, "outcome"),
        replayed | {"outcome": None},
        unsigned,
        _without(unsigned, "signed_by"),
        _without(received, "signed_by"),
    ]
    refused = [
        # A call refused for its signature was not opened, and no pinned key verified it: its line can
        # say nothing of what the envelope held.
        unsigned | {"signed_by": PINNED},
        unsigned | {"kind": "call_tool"},
        unsigned | {"endpoint": "tracker"},
        unsigned | {"tool": "create_issue"},
        unsigned | {"arguments_sha256": received["arguments_sha256"]},
        unsigned | {"write": True},
        received | {"signed_by": "not-a-key"},
        received | {"signed_by": outpost.fingerprint_of(PINNED)},
        _without(refused_line, "refusal"),
        refused_line | {"outcome": "result"},
        refused_line | {"refusal": "because"},
        _without(executed, "outcome"),
        executed | {"outcome": None},
        executed | {"refusal": "busy"},
        under_way | {"refusal": "busy"},
        replayed | {"refusal": "busy"},
        received | {"outcome": "result"},
        received | {"refusal": "busy"},
        executed | {"arguments": {"title": "x"}},
        executed | {"result": {}},
        executed | {"asserted": {"a_name_the_contract_does_not_list": "x"}},
        executed | {"arguments_sha256": "not-a-digest"},
        _without(executed, "at"),
    ]
    for document in accepted:
        assert _both("outpost-audit-line.schema.json", outpost.OutpostAuditLine, document) == (True, True)
    for document in refused:
        assert _both("outpost-audit-line.schema.json", outpost.OutpostAuditLine, document) == (
            False,
            False,
        ), document


def test_an_audit_line_holds_digests_and_never_a_payload() -> None:
    declared = set(contracts.load_schema("outpost-audit-line.schema.json")["properties"])
    assert "arguments_sha256" in declared
    assert not {"arguments", "result", "content", "tools", "error"} & declared
    line = _json("outpost-audit-line.example.json")
    call = _json("outpost-call.example.json")
    assert line["arguments_sha256"] == outpost.arguments_sha256(call["arguments"])
    assert line["asserted"] == call["asserted"]
    # A received call says which pinned key ordered it (ADR 0148 §13).
    assert line["event"] == "received" and line["signed_by"] in _policy()["door_keys"]
    assert "signed_by" in declared and "signature" not in declared
    assert set(outpost.UNOPENED) <= declared


def test_an_enrolment_is_read_the_same_by_the_schema_and_the_model() -> None:
    request = _json("outpost-enrolment-request.example.json")
    accepted = [request, _without(request, "facts")]
    refused = [
        _without(request, "token"),
        _without(request, "signing_key"),
        _without(request, "sealing_key"),
        _without(request, "protocol"),
        request | {"protocol": 0},
        request | {"signing_key": request["signing_key"] + "="},
        request | {"capabilities": ["tools", "runs"]},
        request | {"capabilities": ["relay"]},
        request | {"private_key": "x"},
        request | {"facts": {"hostname": "h" * 257}},
        request | {"facts": {f"fact_{n}": "x" for n in range(17)}},
        request | {"facts": {"Host Name": "x"}},
        request | {"facts": {"nested": {"a": 1}}},
        request | {"token": "short"},
    ]
    schema, model = "outpost-enrolment-request.schema.json", outpost.OutpostEnrolmentRequest
    for document in accepted:
        assert _both(schema, model, document) == (True, True), document
    for document in refused:
        assert _both(schema, model, document) == (False, False), document
    answer = _json("outpost-enrolment-answer.example.json")
    assert answer["status"] == "pending"  # born pending: handed nothing (ADR 0147 §4)
    assert _both(
        "outpost-enrolment-answer.schema.json", outpost.OutpostEnrolmentAnswer, answer | {"status": "active"}
    ) == (False, False)
    assert answer["fingerprint"] == outpost.fingerprint_of(request["signing_key"])


def test_a_poll_is_read_the_same_by_the_door_and_the_model() -> None:
    request = _json("outpost-poll-request.example.json")
    accepted = [
        request,
        _without(request, "wait_s", "endpoints", "running"),
        request | {"wait_s": 0, "capacity": 0},
    ]
    refused = [
        request | {"wait_s": outpost.MAX_WAIT_S + 1},
        request | {"capacity": outpost.MAX_CONCURRENCY + 1},
        request | {"capabilities": ["runs"]},
        request | {"endpoints": ["Tracker"]},
        request | {"running": ["../x"]},
        _without(request, "capacity"),
        _without(request, "protocol"),
        request | {"seq": 41},
    ]
    for document in accepted:
        assert not list(_door("PollRequest").iter_errors(document)), document
        assert _accepts(outpost.OutpostPollRequest, document), document
    for document in refused:
        assert list(_door("PollRequest").iter_errors(document)), document
        assert not _accepts(outpost.OutpostPollRequest, document), document
    assert outpost.OutpostPollRequest.model_validate(_without(request, "wait_s")).wait_s == outpost.MAX_WAIT_S


# ───────────────────────── the closed lists ─────────────────────────


def test_the_refusal_codes_are_one_closed_list_everywhere() -> None:
    codes = [code.value for code in OutpostRefusalCode]
    assert contracts.load_schema("outpost-refusal.schema.json")["properties"]["code"]["enum"] == codes
    assert contracts.load_schema("outpost-audit-line.schema.json")["properties"]["refusal"]["enum"] == [
        *codes,
        None,
    ]
    assert API_SCHEMAS["OutpostRefusalCode"]["enum"] == codes
    described = contracts.load_schema("outpost-refusal.schema.json")["properties"]["code"]["description"]
    assert all(f"`{code}`" in described for code in codes), "a refusal code is not described"
    # A refusal is a code and nothing else: nothing in it can carry the rule that produced it.
    assert set(contracts.load_schema("outpost-refusal.schema.json")["properties"]) == {"code"}
    assert set(outpost.OutpostRefusal.model_fields) == {"code"}


def test_the_enumerations_are_the_documents_lists() -> None:
    assert API_SCHEMAS["OutpostStatus"]["enum"] == [s.value for s in OutpostStatus]
    assert API_SCHEMAS["OutpostInstanceStatus"]["enum"] == [s.value for s in OutpostInstanceStatus]
    assert [s.value for s in OutpostInstanceStatus] == ["pending", "active", "revoked"]
    assert API_SCHEMAS["OutpostCapability"]["enum"] == [c.value for c in OutpostCapability]
    assert API_SCHEMAS["OutpostCallKind"]["enum"] == [k.value for k in OutpostCallKind]
    assert API_SCHEMAS["OutpostCallState"]["enum"] == [s.value for s in OutpostCallState]
    assert OutpostCallState.IN_DOUBT.value == "in_doubt"
    outcomes = [o.value for o in OutpostAnswerOutcome]
    assert API_SCHEMAS["OutpostAnswerOutcome"]["enum"] == outcomes
    assert contracts.load_schema("outpost-answer.schema.json")["properties"]["outcome"]["enum"] == outcomes
    audit = contracts.load_schema("outpost-audit-line.schema.json")["properties"]
    assert audit["outcome"]["enum"] == [*outcomes, None]
    assert audit["event"]["enum"] == [e.value for e in OutpostAuditEvent]


def test_runs_is_reserved_and_no_message_of_protocol_1_carries_it() -> None:
    """ADR 0149 §12: `runs` is a capability no outpost announces until a record decides it."""
    assert [c.value for c in OutpostCapability] == ["tools", "runs"]
    announced = [
        contracts.load_schema("outpost-enrolment-request.schema.json")["properties"]["capabilities"],
        DOOR_SCHEMAS["PollRequest"]["properties"]["capabilities"],
        API_SCHEMAS["OutpostCreate"]["properties"]["capabilities"],
    ]
    for capabilities in announced:
        assert capabilities["items"]["enum"] == ["tools"]
    assert "runs" in API_SCHEMAS["OutpostCapability"]["description"]


def _responses(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """An operation's responses, a `$ref` to the door's components followed."""
    found = {}
    for status, response in operation["responses"].items():
        shared = response.get("$ref", "").rsplit("/", 1)[-1]
        found[status] = DOOR["components"]["responses"][shared] if shared else response
    return found


def test_the_door_codes_are_one_closed_list_with_one_status_each() -> None:
    codes = [code.value for code in OutpostDoorCode]
    assert DOOR_SCHEMAS["DoorProblem"]["properties"]["code"]["enum"] == codes
    assert set(outpost.DOOR_STATUS) == set(OutpostDoorCode)
    answered: dict[str, set[int]] = {}
    for item in DOOR["paths"].values():
        for verb in VERBS & set(item):
            for status, response in _responses(item[verb]).items():
                for code in response.get("x-codes", []):
                    answered.setdefault(code, set()).add(int(status))
    assert set(answered) == set(codes), "a code no verb answers, or a verb answering a code off the list"
    assert {OutpostDoorCode(code): statuses for code, statuses in answered.items()} == {
        code: {status} for code, status in outpost.DOOR_STATUS.items()
    }
    described = DOOR_SCHEMAS["DoorProblem"]["properties"]["code"]["description"]
    for code, status in outpost.DOOR_STATUS.items():
        assert f"`{code.value}`" in described, code
        assert str(status) in described
    # What is not this outpost's answers 404, never 403 (ADR 0148 §6): the two 403s are about the caller.
    assert {c for c, s in outpost.DOOR_STATUS.items() if s == 403} == {
        OutpostDoorCode.INSTANCE_PENDING,
        OutpostDoorCode.INSTANCE_REVOKED,
    }
    assert outpost.DOOR_STATUS[OutpostDoorCode.NOT_FOUND] == 404


def test_a_problem_of_the_door_carries_its_code_and_the_doors_time() -> None:
    assert {"code", "server_time"} <= set(DOOR_SCHEMAS["DoorProblem"]["required"])
    stale = _json("outpost-door-problem.example.json")
    assert stale["code"] == "stale_iat" and stale["status"] == outpost.DOOR_STATUS[OutpostDoorCode.STALE_IAT]
    assert list(_door("DoorProblem").iter_errors(_without(stale, "server_time")))
    assert list(_door("DoorProblem").iter_errors(stale | {"code": "forbidden"}))
    below = outpost.OutpostDoorProblem.model_validate(_json("outpost-door-problem.protocol.example.json"))
    assert below.known_code is OutpostDoorCode.PROTOCOL_UNSUPPORTED and below.status == 426
    assert below.protocol is not None and below.protocol.minimum == 2  # the refusal names the minimum
    # A daemon that meets a code a later door added goes by the status instead of failing on it.
    later = outpost.OutpostDoorProblem.model_validate(stale | {"code": "a_later_code"})
    assert later.known_code is None and later.status == 401


# ───────────────────────── the bounds and the names ─────────────────────────


def test_the_bounds_of_the_document_are_the_constants() -> None:
    bounds = DOOR["x-bounds"]
    assert bounds["max_wait_s"] == outpost.MAX_WAIT_S == 25  # ADR 0148 §7
    assert bounds["max_lease_s"] == outpost.MAX_LEASE_S
    assert bounds["iat_window_s"] == outpost.IAT_WINDOW_S == 60  # ADR 0148 §4
    assert bounds["nonce_ttl_s"] == outpost.NONCE_TTL_S
    assert bounds["enrolment_ttl_s"] == outpost.ENROLMENT_TTL_S == 15 * 60  # ADR 0147 §4
    assert bounds["max_concurrency"] == outpost.MAX_CONCURRENCY
    assert bounds["max_door_keys"] == outpost.MAX_DOOR_KEYS
    assert bounds["max_sealed_call_bytes"] == outpost.MAX_SEALED_CALL_BYTES
    assert bounds["max_body_bytes"] == outpost.MAX_BODY_BYTES
    poll = DOOR_SCHEMAS["PollRequest"]["properties"]
    assert poll["wait_s"]["maximum"] == poll["wait_s"]["default"] == outpost.MAX_WAIT_S
    assert poll["capacity"]["maximum"] == outpost.MAX_CONCURRENCY
    policy = contracts.load_schema("outpost-policy.schema.json")
    assert policy["properties"]["concurrency"]["maximum"] == outpost.MAX_CONCURRENCY
    assert policy["$defs"]["endpoint"]["properties"]["timeout_s"]["maximum"] == outpost.MAX_LEASE_S
    minutes = API_SCHEMAS["OutpostEnrolmentCreate"]["properties"]["expires_in_minutes"]
    assert minutes["maximum"] * 60 == outpost.ENROLMENT_TTL_S and minutes["default"] == minutes["maximum"]


def test_every_verb_has_its_cap_and_refuses_above_it() -> None:
    caps = {}
    for path, item in DOOR["paths"].items():
        operation = item["post"]
        caps[path.rsplit("/", 1)[-1]] = operation["x-max-body-bytes"]
        assert "413" in operation["responses"], path
    assert caps == outpost.MAX_BODY_BYTES
    # The largest answer the platform keeps (1 MiB of JSON) fits once sealed and encoded.
    assert outpost.MAX_BODY_BYTES["answer"] >= 1_048_576 * 4 // 3 + 1024


def test_the_patterns_are_the_same_in_every_document() -> None:
    call_id = DOOR["components"]["parameters"]["CallId"]["schema"]["pattern"]
    assert call_id == outpost.OUTPOST_ID_PATTERN
    assert DOOR_SCHEMAS["SealedCall"]["properties"]["id"]["pattern"] == outpost.OUTPOST_ID_PATTERN
    assert contracts.load_schema("outpost-call.schema.json")["properties"]["id"]["pattern"] == call_id
    assert OPENAPI["components"]["parameters"]["OutpostInstanceId"]["schema"]["pattern"] == call_id
    slug = OPENAPI["components"]["parameters"]["OutpostSlug"]["schema"]["pattern"]
    assert slug == outpost.OUTPOST_SLUG_PATTERN
    assert API_SCHEMAS["OutpostCreate"]["properties"]["slug"]["pattern"] == slug
    assert API_SCHEMAS["McpOutpostConnectorConfig"]["properties"]["outpost"]["pattern"] == slug
    alias = contracts.load_schema("outpost-policy.schema.json")["properties"]["endpoints"]["propertyNames"]
    assert alias["pattern"] == outpost.ENDPOINT_ALIAS_PATTERN
    assert API_SCHEMAS["McpOutpostConnectorConfig"]["properties"]["endpoint"]["pattern"] == alias["pattern"]
    assert DOOR_SCHEMAS["PollRequest"]["properties"]["endpoints"]["items"]["pattern"] == alias["pattern"]
    fingerprint = contracts.load_schema("outpost-enrolment-answer.schema.json")["properties"]["fingerprint"]
    assert fingerprint["pattern"] == outpost.FINGERPRINT_PATTERN
    assert API_SCHEMAS["OutpostInstance"]["properties"]["fingerprint"]["pattern"] == fingerprint["pattern"]
    assert (
        API_SCHEMAS["OutpostInstanceApprove"]["properties"]["fingerprint"]["pattern"]
        == fingerprint["pattern"]
    )
    for sealed in (DOOR_SCHEMAS["SealedCall"], DOOR_SCHEMAS["SealedAnswer"]):
        assert sealed["properties"]["sealed"]["pattern"] == outpost.SEALED_PATTERN


# ───────────────────────── what a signature binds, what a digest covers ─────────────────────────


def test_the_request_signature_is_described_as_the_helpers_build_it() -> None:
    signature = DOOR["x-request-signature"]
    assert "x-signature" not in DOOR, "two signatures cross the door: each section says whose it is"
    assert "instance's" in signature["whose"] and "`x-call-signature`" in signature["whose"]
    assert signature["header"] == outpost.SIGNATURE_HEADER == "Outpost-Signature"
    assert signature["nonce_header"] == outpost.NONCE_HEADER == "Outpost-Nonce"
    assert (signature["algorithm"], signature["type"]) == (
        outpost.SIGNATURE_ALGORITHM,
        outpost.REQUEST_SIGNATURE_TYPE,
    )
    scheme = DOOR["components"]["securitySchemes"]["outpostSignature"]
    assert (scheme["type"], scheme["in"], scheme["name"]) == ("apiKey", "header", outpost.SIGNATURE_HEADER)
    assert set(DOOR["components"]["headers"]) == {"OutpostNonce"}
    body = b'{"protocol":1}'
    enrolment = outpost.request_signature_claims(
        method="post", url="https://o.example/outposts/v1/enrol", body=body, iat=7
    )
    assert list(enrolment) == signature["enrolment_claims"] == list(outpost.REQUEST_SIGNATURE_CLAIMS)
    session = outpost.request_signature_claims(
        method="post", url="https://o.example/outposts/v1/poll", body=body, iat=7, nonce="n", seq=3
    )
    assert list(session) == signature["session_claims"]
    assert signature["session_claims"] == [
        *outpost.REQUEST_SIGNATURE_CLAIMS,
        *outpost.REQUEST_SIGNATURE_SESSION_CLAIMS,
    ]
    # What ADR 0148 §4 says a signature binds: the method, the URL, a digest of the body, `iat`, the
    # server-issued nonce and the per-instance sequence. Each is a claim, and each is described.
    assert set(signature["claims"]) == set(session)
    assert session == {
        "htm": "POST",
        "htu": "https://o.example/outposts/v1/poll",
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "iat": 7,
        "nonce": "n",
        "seq": 3,
    }
    assert outpost.body_sha256(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert outpost.request_signature_header() == {"alg": "EdDSA", "typ": "choregos-outpost+jwt"}
    assert outpost.request_signature_header("i-1")["kid"] == "i-1"
    for check in ("stale_iat", "stale_nonce", "replayed_sequence", "request_mismatch", "invalid_signature"):
        assert f"`{check}`" in signature["checks"], check


# ───────────────────────── a call is signed by the door (ADR 0148 §13) ─────────────────────────


def test_the_call_signature_is_described_as_the_helpers_build_it() -> None:
    signature = DOOR["x-call-signature"]
    assert "door's" in signature["whose"] and "`x-request-signature`" in signature["whose"]
    assert (signature["algorithm"], signature["type"]) == (
        outpost.SIGNATURE_ALGORITHM,
        outpost.CALL_SIGNATURE_TYPE,
    )
    # The two signatures cannot be taken for one another: another `typ`, other claims.
    assert outpost.CALL_SIGNATURE_TYPE == "choregos-outpost-call+jwt" != outpost.REQUEST_SIGNATURE_TYPE
    assert not set(outpost.CALL_SIGNATURE_CLAIMS) & {
        *outpost.REQUEST_SIGNATURE_CLAIMS,
        *outpost.REQUEST_SIGNATURE_SESSION_CLAIMS,
    }
    claims = outpost.call_signature_claims(call_id="c-1", instance_id="i-1", sealed="abc")
    assert list(claims) == signature["claim_names"] == list(outpost.CALL_SIGNATURE_CLAIMS)
    assert signature["claim_names"] == ["call", "instance", "sealed_sha256"]  # and nothing else: no `iat`
    assert set(signature["claims"]) == set(claims)
    assert claims == {
        "call": "c-1",
        "instance": "i-1",
        "sealed_sha256": hashlib.sha256(b"abc").hexdigest(),
    }
    assert outpost.sealed_sha256("abc") == hashlib.sha256(b"abc").hexdigest()
    assert outpost.sealed_sha256("abd") != outpost.sealed_sha256("abc")
    assert "no `iat`" in signature["freshness"] and "`deadline`" in signature["freshness"]
    # `kid` is the signing key itself: nothing but a name travels, the daemon verifies under what it pinned.
    assert outpost.call_signature_header(PINNED) == {
        "alg": "EdDSA",
        "typ": "choregos-outpost-call+jwt",
        "kid": PINNED,
    }
    vector = _json("outpost-sealing.vector.json")["call"]
    assert vector["signature_header"] == outpost.call_signature_header(PINNED)
    assert vector["signature_claims"] == outpost.call_signature_claims(
        call_id=vector["associated_data"], instance_id=vector["instance"], sealed=vector["sealed"]
    )


def test_a_call_crosses_the_door_signed_and_an_unsigned_one_is_still_read_far_enough_to_refuse_it() -> None:
    sealed = DOOR_SCHEMAS["SealedCall"]
    assert sealed["required"] == ["id", "sealed", "signature"]
    assert sealed["properties"]["signature"]["pattern"] == outpost.CALL_SIGNATURE_PATTERN
    handed = _json("outpost-poll-answer.example.json")["calls"][0]
    assert set(handed) == {"id", "sealed", "signature"}
    assert handed["signature"] == _json("outpost-sealing.vector.json")["call"]["signature"]
    assert not list(_door("SealedCall").iter_errors(handed))
    # The door always signs: its document refuses a call without a signature, or with one that is no JWS.
    unsigned = _without(handed, "signature")
    assert list(_door("SealedCall").iter_errors(unsigned))
    assert list(_door("SealedCall").iter_errors(handed | {"signature": "not.a-jws"}))
    assert list(_door("SealedCall").iter_errors(handed | {"signature": handed["signature"] + "A"}))
    assert list(
        _door("PollAnswer").iter_errors(_json("outpost-poll-answer.example.json") | {"calls": [unsigned]})
    )
    # A daemon still reads such a call: it refuses that one call by its id instead of losing the answer.
    answer = _json("outpost-poll-answer.example.json")
    read = outpost.OutpostPollAnswer.model_validate(answer | {"calls": [unsigned, handed]})
    assert [call.signature for call in read.calls] == [None, handed["signature"]]
    assert outpost.OutpostSealedCall.model_validate(handed | {"signature": "garbage"}).signature == "garbage"
    assert OutpostRefusalCode.SIGNATURE_REFUSED.value == "signature_refused"


def test_the_daemon_checks_a_call_before_it_opens_it_and_names_no_check() -> None:
    signature = DOOR["x-call-signature"]
    checks = signature["checks"]
    assert "before it opens the envelope" in checks and "does not open the envelope" in checks
    order = ["pinned keys", "verifies under", "`call` is", "`instance` is", "`sealed_sha256` is"]
    positions = [checks.index(step) for step in order]
    assert positions == sorted(positions), "the order of the daemon's checks moved"
    assert "`signature_refused`" in checks and "never which check failed" in checks
    assert "no `signature`" in checks  # an unsigned call is refused the same way
    described = contracts.load_schema("outpost-refusal.schema.json")["properties"]["code"]["description"]
    assert "`signature_refused`" in described and "not opened" in described
    # One code for the class: no code of the closed list says which check failed.
    about_signatures = [
        code.value for code in OutpostRefusalCode if "sign" in code.value or "pin" in code.value
    ]
    assert about_signatures == ["signature_refused"]
    assert "404" in signature["refusal"]


def test_the_pinned_keys_live_in_the_policy_file_and_no_answer_of_the_door_carries_one() -> None:
    policy = contracts.load_schema("outpost-policy.schema.json")
    assert "door_keys" in policy["required"], "an outpost that cannot check who orders it does not serve"
    keys = policy["properties"]["door_keys"]
    assert (keys["minItems"], keys["maxItems"], keys["uniqueItems"]) == (1, outpost.MAX_DOOR_KEYS, True)
    assert keys["items"]["pattern"] == outpost.PUBLIC_KEY_PATTERN  # the key itself, not a fingerprint
    assert DOOR["x-bounds"]["max_door_keys"] == outpost.MAX_DOOR_KEYS == 4
    assert "no trust on first use" in DOOR["x-call-signature"]["keys"]
    assert "No message of this protocol rotates a pinned key" in DOOR["x-call-signature"]["rotation"]

    def names(node: Any) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {name for value in node.values() for name in names(value)}
        if isinstance(node, list):
            return {name for value in node for name in names(value)}
        return set()

    # Nothing the door sends, at enrolment or later, adds a key to the pinned set or changes it.
    sent_by_the_door = [
        DOOR["paths"],
        DOOR["components"],
        contracts.load_schema("outpost-enrolment-answer.schema.json"),
        contracts.load_schema("outpost-call.schema.json"),
    ]
    for document in sent_by_the_door:
        assert not {"door_keys", "door_key", "signing_keys"} & names(document)
    assert "door_keys" not in _json("outpost-enrolment-answer.example.json")


def test_what_is_not_signed_cannot_make_a_daemon_call_anything() -> None:
    not_signed = DOOR["x-call-signature"]["not_signed"]
    for unsigned in ("`stop`", "`cancel`", "`protocol`", "`server_time`", "enrolment's answer", "problem"):
        assert unsigned in not_signed, unsigned
    assert "cannot make it call anything" in not_signed and "nor read an answer" in not_signed
    # `server_time` is not signed: a deadline is judged by the outpost's own clock.
    assert "own clock" in not_signed
    deadline = contracts.load_schema("outpost-call.schema.json")["properties"]["deadline"]["description"]
    assert "own clock" in deadline and "never push it away" in deadline
    # A replayed call reaches no server: the journal before its deadline, `deadline_passed` after it.
    delivery = DOOR["x-bounds"]["delivery"]
    assert "one decision per call id" in delivery and "journal" in delivery
    assert "`deadline_passed`" in delivery and "replayed" in delivery
    assert "No refusal is sent for a copy" in delivery
    assert OutpostRefusalCode.DEADLINE_PASSED.value == "deadline_passed"
    # Sealing alone proves no origin, and the document no longer leaves it at that.
    sealing = DOOR["x-sealing"]
    assert "not_proof_of_origin" not in sealing
    assert "proves no origin" in sealing["origin"] and "`x-call-signature`" in sealing["origin"]


# ───────────────────────── what building against the contract found (S65-10b) ─────────────────────────


def test_the_journal_decides_first_and_the_document_says_which_sentence_wins() -> None:
    checks, delivery = DOOR["x-call-signature"]["checks"], DOOR["x-bounds"]["delivery"]
    for text in (checks, delivery):
        assert "journal decides first, always" in text
    assert checks.startswith("The journal decides first, always")
    assert "the journal's sentence wins" in checks
    # A live entry: its decision stands, and no refusal is sent for the copy.
    assert "live entry gets what the journal holds, and no refusal is sent for the copy" in checks
    assert "a call under way gets nothing yet" in delivery
    # A lapsed entry: `deadline_passed`, without the copy being verified.
    assert "lapsed entry" in checks and "refused `deadline_passed` without being verified" in checks
    assert "a copy is refused `deadline_passed` without being verified" in delivery
    assert "nothing left to protect" in delivery and "`lease_lost`" in delivery
    # The signature's checks are for an id the journal does not hold, and only for that one.
    assert checks.index("Only for an id the journal does not hold") < checks.index("pinned keys")
    assert "An id the journal does not hold is a new call: verified, opened" in delivery
    # The sentence that said a lapsed read is made again under the same id is gone: it lost.
    assert "no longer counts" not in delivery
    assert "another instance, or under a new id" in delivery
    lapsed = contracts.load_schema("outpost-refusal.schema.json")["properties"]["code"]["description"]
    assert "met an entry of the journal that had lapsed" in lapsed


def test_a_daemon_can_bound_what_a_poll_hands_over() -> None:
    bounds = DOOR["x-bounds"]
    assert bounds["max_sealed_call_bytes"] == outpost.MAX_SEALED_CALL_BYTES == 2_097_152
    assert outpost.MAX_BODY_BYTES["answer"] == outpost.MAX_SEALED_CALL_BYTES  # the same 2 MiB as an answer
    assert bounds["max_poll_answer_rest_bytes"] == outpost.MAX_POLL_ANSWER_REST_BYTES == 65_536
    assert bounds["max_running"] == outpost.MAX_RUNNING
    handed = bounds["handed_over"]
    assert "64 KiB plus `capacity` times 2 MiB" in handed
    assert "refused where it is admitted" in handed and "never handed over" in handed
    answer, sealed = DOOR_SCHEMAS["PollAnswer"]["properties"], DOOR_SCHEMAS["SealedCall"]["properties"]
    assert sealed["sealed"]["maxLength"] == outpost.MAX_SEALED_CALL_BYTES
    assert answer["calls"]["maxItems"] == outpost.MAX_CONCURRENCY
    assert answer["cancel"]["maxItems"] == outpost.MAX_RUNNING
    assert DOOR_SCHEMAS["PollRequest"]["properties"]["running"]["maxItems"] == outpost.MAX_RUNNING
    assert answer["cancel"]["items"]["pattern"] == outpost.OUTPOST_ID_PATTERN
    # What a daemon bounds its reading to, before it parses anything.
    assert outpost.max_poll_answer_bytes(0) == 65_536
    assert outpost.max_poll_answer_bytes(7) == 65_536 + 7 * 2_097_152
    for capacity in (-1, outpost.MAX_CONCURRENCY + 1):
        with pytest.raises(ValueError, match="0 to 64"):
            outpost.max_poll_answer_bytes(capacity)
    # The arithmetic holds: the heaviest answer without a call fits in the 64 KiB, and a call as heavy as
    # the bound, id and signature included, is still a call the document and the model take.
    longest_id = "c" * 128
    rest = _json("outpost-poll-answer.example.json") | {
        "calls": [],
        "cancel": [longest_id[:-3] + f"{n:03d}" for n in range(outpost.MAX_RUNNING)],
    }
    assert not list(_door("PollAnswer").iter_errors(rest))
    assert len(json.dumps(rest).encode("utf-8")) <= outpost.MAX_POLL_ANSWER_REST_BYTES
    handed_call = _json("outpost-poll-answer.example.json")["calls"][0]
    around = len(json.dumps(handed_call | {"id": longest_id, "sealed": ""}).encode("utf-8")) + 2
    heaviest = handed_call | {"id": longest_id, "sealed": "A" * (outpost.MAX_SEALED_CALL_BYTES - around)}
    assert len(json.dumps(heaviest).encode("utf-8")) <= outpost.MAX_SEALED_CALL_BYTES
    assert not list(_door("SealedCall").iter_errors(heaviest))
    assert _accepts(outpost.OutpostSealedCall, heaviest)
    too_heavy = handed_call | {"sealed": "A" * (outpost.MAX_SEALED_CALL_BYTES + 1)}
    assert list(_door("SealedCall").iter_errors(too_heavy))
    assert not _accepts(outpost.OutpostSealedCall, too_heavy)
    # More calls than an instance may hold, or more cancellations than it may report, is not an answer.
    full = _json("outpost-poll-answer.example.json")
    for field, most in (("calls", outpost.MAX_CONCURRENCY), ("cancel", outpost.MAX_RUNNING)):
        over = full | {field: [full[field][0]] * (most + 1)}
        assert list(_door("PollAnswer").iter_errors(over)), field
        assert not _accepts(outpost.OutpostPollAnswer, over), field


def test_a_cancel_never_interrupts_a_write_that_reached_its_server() -> None:
    for text in (
        DOOR["x-bounds"]["cancel"],
        DOOR_SCHEMAS["PollAnswer"]["properties"]["cancel"]["description"],
    ):
        assert "not yet started is refused `cancelled`" in text
        assert "read in flight is abandoned and answered `unanswered`" in text
        assert "`lease_lost`" in text
    assert "A cancel never interrupts a write that has reached its server" in DOOR["x-bounds"]["cancel"]
    assert "the write finishes, the daemon records its answer and sends it" in DOOR["x-bounds"]["cancel"]
    outcome = contracts.load_schema("outpost-answer.schema.json")["properties"]["outcome"]["description"]
    assert "a read abandoned because the door cancelled it" in outcome
    assert OutpostRefusalCode.CANCELLED.value == "cancelled"
    assert OutpostAnswerOutcome.UNANSWERED.value == "unanswered"
    assert outpost.DOOR_STATUS[OutpostDoorCode.LEASE_LOST] == 409


def test_what_an_outpost_cannot_record_it_does_not_do() -> None:
    codes = [code.value for code in OutpostRefusalCode]
    assert "cannot_record" in codes and len(codes) == 12
    described = contracts.load_schema("outpost-refusal.schema.json")["properties"]["code"]["description"]
    assert "`cannot_record`: the outpost could not write its journal or its audit line" in described
    # `busy` also covers a call claimed and never begun.
    assert "claimed and never began because it was stopping or was restarted" in described
    management = API_SCHEMAS["OutpostRefusalCode"]["description"]
    assert (
        "`cannot_record`" in management and "`busy` also covers a call claimed and never begun" in management
    )
    assert not list(_file("outpost-refusal.schema.json").iter_errors({"code": "cannot_record"}))
    line = {"at": "2026-11-12T10:05:00Z", "event": "refused", "call_id": "4a1c", "refusal": "cannot_record"}
    assert _both("outpost-audit-line.schema.json", outpost.OutpostAuditLine, line) == (True, True)


def test_a_daemon_numbers_from_its_clock_and_the_door_gives_no_hint() -> None:
    sequence = DOOR["x-request-signature"]["claims"]["seq"]
    assert "number from its clock in milliseconds" in sequence
    assert "never below its last sequence plus one" in sequence
    assert "64-bit integer" in sequence and "carries no hint" in sequence
    now = 1_794_477_600_000
    assert outpost.next_sequence(None, now) == now  # a lost state file: the clock is past every number sent
    assert outpost.next_sequence(now - 5, now) == now
    assert outpost.next_sequence(now, now) == now + 1  # two requests in one millisecond
    assert outpost.next_sequence(now + 40, now) == now + 41  # a clock set back never numbers backwards
    assert outpost.next_sequence(None, now) > 10**6 * 1000  # above any count of requests made one by one
    assert outpost.MAX_SEQUENCE == 2**53 - 1 and now < outpost.MAX_SEQUENCE < 2**63
    for last, clock in ((None, 0), (outpost.MAX_SEQUENCE, now)):
        with pytest.raises(ValueError, match="whole number"):
            outpost.next_sequence(last, clock)
    # `replayed_sequence` says nothing of the number the door holds.
    problem = set(DOOR_SCHEMAS["DoorProblem"]["properties"])
    assert not {"seq", "sequence", "last_sequence", "expected_sequence"} & problem
    assert not {"seq", "sequence", "last_sequence"} & set(outpost.OutpostDoorProblem.model_fields)


def test_the_doors_clock_is_information_and_never_corrects_iat() -> None:
    signature = DOOR["x-request-signature"]
    assert "by the daemon's own clock" in signature["claims"]["iat"]
    assert "never corrects `iat` from `server_time`" in signature["claims"]["iat"]
    clock = signature["server_time"]
    assert "It is information" in clock and "`doctor`" in clock
    assert "bring a call's deadline closer, never further" in clock
    assert "A daemon never corrects `iat` from it" in clock


def test_a_revoked_key_does_not_come_back_for_as_long_as_its_outpost_exists() -> None:
    enrol = DOOR["paths"]["/outposts/v1/enrol"]["post"]["description"]
    assert "a revoked key does not come back, for as long as its outpost exists" in enrol
    assert "memory of their keys" in enrol and "a new enrolment like any other" in enrol
    delete = _management()[("/orgs/{org}/outposts/{slug}", "delete")]["description"]
    assert "the memory of their keys" in delete and "enrols as any new key does" in delete


def test_the_door_cannot_open_an_answer_and_does_not_write_its_digest() -> None:
    answer = DOOR["paths"]["/outposts/v1/calls/{id}/answer"]["post"]["description"]
    assert "The door cannot open an answer" in answer and "does not need to open" not in answer
    assert "The door cannot open it" in DOOR["x-sealing"]["answer"]
    digest = API_SCHEMAS["OutpostCallRecord"]["properties"]["result_sha256"]
    assert digest["type"] == ["string", "null"]
    assert "written by whoever opens the answer" in digest["description"]
    assert (
        "never the door" in digest["description"] and "Null for a call nobody opened" in digest["description"]
    )


def test_no_enrolment_token_without_a_key_for_the_outpost_to_pin() -> None:
    conflict = API_SCHEMAS["OutpostConflict"]
    assert "door_key_missing" in conflict["properties"]["code"]["enum"]
    assert "`door_key_missing` (outposts are on and the door has no signing key" in conflict["description"]
    enrolments = _management()[("/orgs/{org}/outposts/{slug}/enrolments", "post")]
    assert "`door_key_missing`" in enrolments["description"]
    assert enrolments["responses"]["409"] == {"$ref": "#/components/responses/OutpostConflict"}
    # It is why the token's answer always has a key: an empty list is not an answer.
    assert API_SCHEMAS["OutpostEnrolmentCreated"]["properties"]["door_keys"]["minItems"] == 1
    # `outposts_disabled` stays what it says.
    assert "`outposts_disabled` (the deployment keeps outposts switched off)" in conflict["description"]
    v = _api("OutpostConflict")
    problem = {"type": "about:blank", "title": "State conflict", "status": 409, "code": "door_key_missing"}
    assert not list(v.iter_errors(problem))


def test_a_path_parameter_with_a_pattern_may_answer_422() -> None:
    patterned = {
        name
        for name, parameter in OPENAPI["components"]["parameters"].items()
        if parameter.get("in") == "path" and "pattern" in parameter.get("schema", {})
    }
    assert {"OutpostSlug", "OutpostInstanceId"} <= patterned
    checked = 0
    for (path, verb), operation in _management().items():
        refs = {
            p["$ref"].rsplit("/", 1)[-1] for p in OPENAPI["paths"][path].get("parameters", []) if "$ref" in p
        }
        if not refs & {"OutpostSlug", "OutpostInstanceId"}:
            assert path == "/orgs/{org}/outposts", path
            continue
        checked += 1
        assert operation["responses"].get("422") == {"$ref": "#/components/responses/ValidationError"}, (
            f"{verb} {path}: a slug or an instance id that does not match its pattern is a 422"
        )
    assert checked == 10


def test_an_instance_id_and_a_token_are_opaque_to_a_daemon() -> None:
    """ADR 0148 §6: the door reads in them which outpost they are of; a daemon reads nothing in them."""
    answer = contracts.load_schema("outpost-enrolment-answer.schema.json")["properties"]["instance_id"]
    request = contracts.load_schema("outpost-enrolment-request.schema.json")["properties"]["token"]
    for field in (answer, request):
        assert "Opaque to a daemon" in field["description"] and "ADR 0148 §6" in field["description"]
    assert answer["pattern"] == outpost.OUTPOST_ID_PATTERN


def test_a_key_has_one_spelling() -> None:
    """Keys are compared as written (`kid` against `door_keys`), so a key must have one spelling: the
    last of its 43 characters carries four bits, and the pattern refuses the 48 others."""
    import base64

    pattern = re.compile(outpost.PUBLIC_KEY_PATTERN)
    for last in range(256):
        raw = bytes(range(31)) + bytes([last])
        spelt = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        assert len(spelt) == 43 and pattern.fullmatch(spelt), spelt
    final = {base64.urlsafe_b64encode(bytes(31) + bytes([n])).decode().rstrip("=")[-1] for n in range(256)}
    assert len(final) == 16
    assert not pattern.fullmatch(PINNED[:-1] + "x") and pattern.fullmatch(PINNED)
    assert not pattern.fullmatch(PINNED + "=") and not pattern.fullmatch(PINNED[:-1])
    everywhere = [
        contracts.load_schema("outpost-policy.schema.json")["properties"]["door_keys"]["items"],
        contracts.load_schema("outpost-enrolment-request.schema.json")["properties"]["signing_key"],
        contracts.load_schema("outpost-enrolment-request.schema.json")["properties"]["sealing_key"],
        contracts.load_schema("outpost-call.schema.json")["properties"]["answer_key"],
        contracts.load_schema("outpost-audit-line.schema.json")["properties"]["signed_by"],
        API_SCHEMAS["Outpost"]["properties"]["door_keys"]["items"],
        API_SCHEMAS["OutpostEnrolmentCreated"]["properties"]["door_keys"]["items"],
    ]
    for field in everywhere:
        assert field["pattern"] == outpost.PUBLIC_KEY_PATTERN
    for example in ("outpost-enrolment-request.example.json", "outpost-call.example.json"):
        document = _json(example)
        for name in ("signing_key", "sealing_key", "answer_key"):
            assert name not in document or pattern.fullmatch(document[name]), (example, name)


def test_a_fingerprint_is_the_digest_of_the_raw_signing_key() -> None:
    key = _json("outpost-enrolment-request.example.json")["signing_key"]
    fingerprint = outpost.fingerprint_of(key)
    assert re.fullmatch(outpost.FINGERPRINT_PATTERN, fingerprint)
    assert fingerprint == _json("outpost-signature.vector.json")["fingerprint"]
    assert outpost.fingerprint_of("A" * 43) != fingerprint
    with pytest.raises(ValueError, match="32 bytes"):
        outpost.fingerprint_of("AAAA")


def test_the_digest_of_arguments_is_of_their_canonical_json() -> None:
    assert outpost.arguments_sha256({"b": 1, "a": "Δ"}) == outpost.arguments_sha256({"a": "Δ", "b": 1})
    assert outpost.arguments_sha256({}) == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    assert outpost.arguments_sha256({"a": 1}) != outpost.arguments_sha256({"a": "1"})


def test_the_protocol_is_1_and_the_window_is_negotiated_at_enrol_and_at_every_poll() -> None:
    assert DOOR["x-protocol"]["version"] == outpost.OUTPOST_PROTOCOL == 1
    assert DOOR["info"]["version"] == f"v{outpost.OUTPOST_PROTOCOL}"
    assert all(path.startswith(outpost.OUTPOST_DOOR_PREFIX + "/") for path in DOOR["paths"])
    assert "protocol" in contracts.load_schema("outpost-enrolment-request.schema.json")["required"]
    assert "protocol" in DOOR_SCHEMAS["PollRequest"]["required"]
    assert "protocol" in contracts.load_schema("outpost-enrolment-answer.schema.json")["required"]
    assert "protocol" in DOOR_SCHEMAS["PollAnswer"]["required"]
    assert set(DOOR_SCHEMAS["ProtocolWindow"]["required"]) == {"current", "minimum"}
    for verb in ("enrol", "poll"):
        refusal = _responses(DOOR["paths"][f"{outpost.OUTPOST_DOOR_PREFIX}/{verb}"]["post"])["426"]
        assert refusal["x-codes"] == ["protocol_unsupported"]


# ───────────────────────── the management API (openapi.yaml) ─────────────────────────

MANAGEMENT = {
    ("/orgs/{org}/outposts", "get"): "listOutposts",
    ("/orgs/{org}/outposts", "post"): "createOutpost",
    ("/orgs/{org}/outposts/{slug}", "get"): "getOutpost",
    ("/orgs/{org}/outposts/{slug}", "delete"): "deleteOutpost",
    ("/orgs/{org}/outposts/{slug}/pause", "post"): "pauseOutpost",
    ("/orgs/{org}/outposts/{slug}/resume", "post"): "resumeOutpost",
    ("/orgs/{org}/outposts/{slug}/revoke", "post"): "revokeOutpost",
    ("/orgs/{org}/outposts/{slug}/enrolments", "post"): "createOutpostEnrolment",
    ("/orgs/{org}/outposts/{slug}/instances", "get"): "listOutpostInstances",
    ("/orgs/{org}/outposts/{slug}/instances/{instance_id}/approve", "post"): "approveOutpostInstance",
    ("/orgs/{org}/outposts/{slug}/instances/{instance_id}/revoke", "post"): "revokeOutpostInstance",
    ("/orgs/{org}/outposts/{slug}/calls", "get"): "listOutpostCalls",
}
CONFLICTS = [
    "outposts_disabled",
    "door_key_missing",
    "outpost_exists",
    "outpost_revoked",
    "outpost_not_revoked",
    "outpost_in_use",
    "instance_not_pending",
    "fingerprint_mismatch",
    "outpost_paused",
    "outpost_offline",
    "outpost_refused",
    "outpost_timeout",
]


def _management() -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, verb): operation
        for path, item in OPENAPI["paths"].items()
        if "/outposts" in path
        for verb, operation in item.items()
        if verb in VERBS
    }


def test_the_management_operations_and_their_routes() -> None:
    assert {key: operation["operationId"] for key, operation in _management().items()} == MANAGEMENT
    for (path, verb), operation in _management().items():
        assert operation["tags"] == ["outposts"], path
        assert "security" not in operation, f"{verb} {path}: a session or an API token, like its neighbours"
        assert "403" in operation["responses"], f"{verb} {path}: without `outpost:manage` it is a 403"
        assert "`outpost:manage`" in operation["description"], f"{verb} {path}"
        if "{slug}" in path:
            assert "404" in operation["responses"], f"{verb} {path}"
    assert {"name": "outposts"} in OPENAPI["tags"]
    # No management route sits under the door's prefix, and the door is not in this document.
    assert not [path for path in OPENAPI["paths"] if path.startswith("/outposts")]


def test_an_instance_is_approved_by_a_person_against_its_fingerprint() -> None:
    """ADR 0147 §4: a token cannot approve; a stolen enrolment token enrols an instance nobody approves."""
    approve = _management()[("/orgs/{org}/outposts/{slug}/instances/{instance_id}/approve", "post")]
    assert approve["requestBody"]["required"] is True
    assert API_SCHEMAS["OutpostInstanceApprove"]["required"] == ["fingerprint"]
    assert "step_up_required" in approve["responses"]["401"]["description"]
    assert "API token cannot approve" in approve["description"]
    v = _api("OutpostInstanceApprove")
    fingerprint = _json("outpost-enrolment-answer.example.json")["fingerprint"]
    assert not list(v.iter_errors({"fingerprint": fingerprint}))
    assert list(v.iter_errors({}))
    assert list(v.iter_errors({"fingerprint": fingerprint.removeprefix("SHA256:")}))
    assert list(v.iter_errors({"fingerprint": fingerprint, "force": True}))


def _refs(node: Any) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if "$ref" in node else []
        return found + [ref for value in node.values() for ref in _refs(value)]
    if isinstance(node, list):
        return [ref for value in node for ref in _refs(value)]
    return []


def test_the_enrolment_token_is_shown_once_and_by_one_route() -> None:
    holders = [
        name
        for name, schema in API_SCHEMAS.items()
        if name.startswith("Outpost") and "token" in schema.get("properties", {})
    ]
    assert holders == ["OutpostEnrolmentCreated"]
    users = [
        key
        for key, operation in _management().items()
        if "#/components/schemas/OutpostEnrolmentCreated" in _refs(operation)
    ]
    assert users == [("/orgs/{org}/outposts/{slug}/enrolments", "post")]
    assert "only once" in API_SCHEMAS["OutpostEnrolmentCreated"]["properties"]["token"]["description"]
    v = _api("OutpostEnrolmentCreate")
    assert not list(v.iter_errors({})) and not list(v.iter_errors({"expires_in_minutes": 5}))
    assert list(v.iter_errors({"expires_in_minutes": 16}))  # 15 minutes at most
    assert list(v.iter_errors({"expires_in_minutes": 0}))


def test_the_keys_to_pin_are_shown_where_a_person_creates_and_enrols() -> None:
    """ADR 0148 §13: the console and the CLI print the lines to put in the policy file; the door's own
    answers never carry them."""
    for name, least in (("Outpost", None), ("OutpostEnrolmentCreated", 1)):
        schema = API_SCHEMAS[name]
        assert "door_keys" in schema["required"], name
        keys = schema["properties"]["door_keys"]
        assert keys["type"] == "array" and keys["maxItems"] == outpost.MAX_DOOR_KEYS, name
        assert keys.get("minItems") == least, name
        assert "first" in keys["description"] and "rotation" in keys["description"], name
    created = {
        "id": "e-1",
        "token": "shown-once",
        "expires_at": "2026-11-12T10:15:00Z",
        "control_plane": _policy()["control_plane"],
        "door_keys": _policy()["door_keys"],
    }
    v = _api("OutpostEnrolmentCreated")
    assert not list(v.iter_errors(created))
    assert list(v.iter_errors(_without(created, "door_keys")))
    assert list(v.iter_errors(created | {"door_keys": []}))
    assert list(v.iter_errors(created | {"door_keys": ["not-a-key"]}))
    assert list(v.iter_errors(created | {"door_keys": [PINNED, *_other_keys(outpost.MAX_DOOR_KEYS)]}))
    # Public halves only: nothing here could sign.
    for name in ("Outpost", "OutpostEnrolmentCreated", "OutpostInstance"):
        assert not {"door_private_key", "signing_private_key", "private_key"} & set(
            API_SCHEMAS[name]["properties"]
        )
    assert "signature_refused" in API_SCHEMAS["OutpostRefusalCode"]["description"]


def test_the_platform_stores_nothing_of_the_outposts_side() -> None:
    """ADR 0147 §7: an alias, never a URL or a credential. ADR 0148 §11: digests, never a payload."""
    forbidden = {
        "url",
        "address",
        "host",
        "secret",
        "secret_refs",
        "secret_values",
        "password",
        "private_key",
    }
    for name in (
        "Outpost",
        "OutpostCreate",
        "OutpostInstance",
        "OutpostCallRecord",
        "McpOutpostConnectorConfig",
    ):
        schema = API_SCHEMAS[name]
        assert schema["additionalProperties"] is False, name
        assert not forbidden & set(schema["properties"]), name
    assert not {"signing_key", "sealing_key"} & set(API_SCHEMAS["OutpostInstance"]["properties"])
    call = set(API_SCHEMAS["OutpostCallRecord"]["properties"])
    assert not {"arguments", "result", "content", "sealed"} & call
    assert {"state", "outcome", "refusal", "arguments_sha256", "result_sha256"} <= call


def test_a_connector_at_an_outpost_names_an_outpost_and_an_alias_and_nothing_else() -> None:
    """ADR 0149 §1: `{outpost, endpoint, timeout_s}`, no URL and no secret field."""
    config = API_SCHEMAS["McpOutpostConnectorConfig"]
    assert set(config["properties"]) == {"outpost", "endpoint", "timeout_s"}
    assert config["required"] == ["outpost", "endpoint"]
    v = _api("McpOutpostConnectorConfig")
    assert not list(v.iter_errors({"outpost": "factory-floor", "endpoint": "tracker"}))
    assert not list(v.iter_errors({"outpost": "factory-floor", "endpoint": "tracker", "timeout_s": 45}))
    assert list(v.iter_errors({"outpost": "factory-floor", "endpoint": "tracker", "url": "https://x"}))
    assert list(v.iter_errors({"outpost": "factory-floor", "endpoint": "tracker", "token": "x"}))
    assert list(v.iter_errors({"outpost": "factory-floor"}))
    assert list(v.iter_errors({"outpost": "factory-floor", "endpoint": "tracker", "timeout_s": 121}))
    for holder in ("OrgConnector", "OrgConnectorCreate"):
        assert "McpOutpostConnectorConfig" in API_SCHEMAS[holder]["properties"]["config"]["description"]


def test_outpost_conflicts_name_their_code() -> None:
    conflict = API_SCHEMAS["OutpostConflict"]
    assert conflict["properties"]["code"]["enum"] == CONFLICTS
    assert all(f"`{code}`" in conflict["description"] for code in CONFLICTS)
    ref = {"$ref": "#/components/responses/OutpostConflict"}
    answering = sorted(o["operationId"] for o in _management().values() if o["responses"].get("409") == ref)
    assert answering == [
        "approveOutpostInstance",
        "createOutpost",
        "createOutpostEnrolment",
        "deleteOutpost",
        "pauseOutpost",
        "resumeOutpost",
    ]
    # Revoking answers a key that may be stolen: nothing stands in its way (ADR 0147 §5).
    for operation in ("revokeOutpost", "revokeOutpostInstance"):
        responses = next(o for o in _management().values() if o["operationId"] == operation)["responses"]
        assert "409" not in responses, operation
    v = _api("OutpostConflict")
    problem = {"type": "about:blank", "title": "State conflict", "status": 409}
    in_use = problem | {"code": "outpost_in_use", "connectors": ["tracker-at-the-factory"]}
    assert not list(v.iter_errors(in_use))
    refused = problem | {
        "code": "outpost_refused",
        "outpost": "factory-floor",
        "endpoint": "tracker",
        "refusal": "write_denied",
    }
    assert not list(v.iter_errors(refused))
    assert list(v.iter_errors(refused | {"refusal": "the rule on line 4"}))
    assert list(v.iter_errors(problem | {"code": "gateway_in_use"}))
    assert list(v.iter_errors(problem))
    # The API always adds `instance` to a problem, and may add `errors` and `retry_after`.
    fuller = in_use | {"instance": "/api/v1/orgs/acme/outposts/factory-floor", "errors": [], "retry_after": 3}
    assert not list(v.iter_errors(fuller))


def test_recent_calls_are_paged_and_filtered_by_state() -> None:
    calls = _management()[("/orgs/{org}/outposts/{slug}/calls", "get")]
    assert {"$ref": "#/components/parameters/Cursor"} in calls["parameters"]
    assert {"$ref": "#/components/parameters/Limit"} in calls["parameters"]
    state = next(p for p in calls["parameters"] if p.get("name") == "state")
    assert state["schema"] == {"$ref": "#/components/schemas/OutpostCallState"}
    assert API_SCHEMAS["OutpostCallPage"]["required"] == ["items", "meta"]
