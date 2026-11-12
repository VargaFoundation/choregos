# SPDX-License-Identifier: Apache-2.0
"""The policy file: the daemon does not start on an invalid one, `door_keys` included (ADR 0147 §6)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_contracts import schemas_dir
from choregos_contracts.outpost import OutpostEndpoint
from choregos_outpost import policy as policies

EXAMPLE = schemas_dir() / "examples" / "outpost-policy.example.yaml"


def _example() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    return loaded


def test_the_contracts_example_is_a_policy_the_daemon_enforces() -> None:
    policy = policies.load(EXAMPLE)
    assert policy.control_plane == "https://outposts.example.com"
    assert policy.door_keys == ["3k9fZti-P3Z9Du9RmNmJm-pNxgUQSHfGN_bx7DmXwNw"]
    tracker = policy.endpoints["tracker"]
    assert tracker.writes == "allow" and tracker.timeout_s == 60 and policies.ceiling(tracker) == 512 * 1024
    assert policies.faults_of(_example()) == []


BROKEN: dict[str, tuple[Any, str]] = {
    "no door keys": (lambda p: p.pop("door_keys"), "door_keys"),
    "an empty list of door keys": (lambda p: p.update(door_keys=[]), "door_keys"),
    "a door key that is not a key": (lambda p: p.update(door_keys=["not-a-key"]), "door_keys.0"),
    "a door key one character short": (lambda p: p.update(door_keys=[p["door_keys"][0][:-1]]), "door_keys.0"),
    "a fingerprint where the key belongs": (
        lambda p: p.update(door_keys=["SHA256:09wT63TkuDIeTj6MxD5TFt7dxaJNpfKNrUkVyJymRJE"]),
        "door_keys.0",
    ),
    "the same door key twice": (lambda p: p.update(door_keys=p["door_keys"] * 2), "door_keys"),
    "five door keys": (
        lambda p: p.update(door_keys=[c + p["door_keys"][0][1:] for c in "ABCDE"]),
        "door_keys",
    ),
    "a control plane with a path": (
        lambda p: p.update(control_plane="https://outposts.example.com/x"),
        "control_plane",
    ),
    "an endpoint with credentials in its url": (
        lambda p: p["endpoints"]["tracker"].update(url="http://user:secret@tracker/mcp"),
        "endpoints.tracker.url",
    ),
    "a credential written in the file": (
        lambda p: p["endpoints"]["tracker"].update(auth={"token": "in-clear"}),
        "endpoints.tracker.auth",
    ),
    "a rule on a name the control plane asserts": (
        lambda p: p["endpoints"]["tracker"].update(projects=["billing-api"]),
        "endpoints.tracker",
    ),
    "no audit log": (lambda p: p.pop("audit"), "audit"),
    "an endpoint without its patterns": (
        lambda p: p["endpoints"]["tracker"].pop("tools"),
        "endpoints.tracker.tools",
    ),
    "an unknown field": (lambda p: p.update(telemetry={"enabled": True}), "(the document)"),
    "a concurrency above the contract's": (lambda p: p.update(concurrency=65), "concurrency"),
}


@pytest.mark.parametrize(("change", "where"), BROKEN.values(), ids=BROKEN.keys())
def test_a_broken_policy_is_refused_with_the_path_of_its_field(
    tmp_path: Path, change: Any, where: str
) -> None:
    document = copy.deepcopy(_example())
    change(document)
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(policies.PolicyError) as refused:
        policies.load(path)
    assert any(fault.startswith(where + ":") for fault in refused.value.faults), refused.value.faults
    assert str(path) in str(refused.value)


def test_a_file_that_is_absent_or_is_not_a_policy_is_refused(tmp_path: Path) -> None:
    with pytest.raises(policies.PolicyError, match="No such file"):
        policies.load(tmp_path / "missing.yaml")
    for text in ("- a list\n- of things\n", "control_plane: [unclosed", ""):
        path = tmp_path / "policy.yaml"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(policies.PolicyError):
            policies.load(path)


def test_every_door_key_must_parse_as_a_key() -> None:
    good = _example()["door_keys"][0]
    assert policies.door_key_faults([good]) == []
    faults = policies.door_key_faults([good, "A" * 42, 12, "!" * 43, "A" * 44])
    assert [fault.split(":")[0] for fault in faults] == [
        "door_keys.1",
        "door_keys.2",
        "door_keys.3",
        "door_keys.4",
    ]


def _endpoint(*patterns: str) -> OutpostEndpoint:
    return OutpostEndpoint(url="http://tracker/mcp", tools=list(patterns))


def test_a_star_stands_for_any_run_of_characters_and_nothing_else_is_special() -> None:
    reads = _endpoint("search_*", "get_*", "create_issue")
    assert policies.allows(reads, "search_issues") and policies.allows(reads, "search_")
    assert policies.allows(reads, "create_issue")
    # The whole name must match.
    assert not policies.allows(reads, "create_issue_and_delete") and not policies.allows(
        reads, "x_search_issues"
    )
    assert not policies.allows(reads, "delete_project") and not policies.allows(reads, "")
    # `.` is a dot and nothing more.
    dotted = _endpoint("a.b", "x*z", "*.read")
    assert policies.allows(dotted, "a.b") and not policies.allows(dotted, "aXb")
    assert (
        policies.allows(dotted, "xz")
        and policies.allows(dotted, "x-anything-z")
        and not policies.allows(dotted, "xzy")
    )
    assert policies.allows(dotted, "issues.read") and not policies.allows(dotted, "issues.write")
    # An empty list serves none; a lone star serves all.
    assert not policies.allows(_endpoint(), "search_issues")
    assert policies.allows(_endpoint("*"), "anything.at-all_1")


def test_a_payload_weighs_the_bytes_of_its_compact_json() -> None:
    assert policies.weight({}) == 2
    assert policies.weight({"a": "é"}) == len('{"a":"é"}'.encode())
    assert policies.weight({"query": "x" * 1012}) == 1024
    assert policies.ceiling(OutpostEndpoint(url="http://t/mcp", tools=[])) == 256 * 1024
