# SPDX-License-Identifier: Apache-2.0
"""The outpost's policy file: what the daemon checks alone (ADR 0147 §6).

The file is the operator's. The control plane cannot write it, and nothing the door sends changes
what it says. It pins the door's keys whose calls the outpost makes (ADR 0148 §13), and it holds
the only copy of each endpoint's address and credential (ADR 0147 §7).

A file that is not valid is not a policy, and the daemon does not start on it: an outpost that
cannot check who orders it does not serve (ADR 0101). Valid means three things, checked in this
order and all reported: the document is what `outpost-policy.schema.json` describes, the model
`OutpostPolicy` reads it, and every entry of `door_keys` parses as an Ed25519 public key.

Deny by default: an alias the file does not hold, a tool outside an endpoint's patterns, a write
where `writes` is not `allow`, a payload above the ceiling are each refused by the daemon, with one
code of the contract's closed list.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from pathlib import Path
from typing import Any

import yaml
from choregos_contracts import load_schema
from choregos_contracts.outpost import OutpostEndpoint, OutpostPolicy
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator
from pydantic import ValidationError

POLICY_SCHEMA = "outpost-policy.schema.json"


class PolicyError(Exception):
    """The policy file is absent, unreadable or invalid. `faults` names each one by its field."""

    def __init__(self, path: Path, faults: list[str]) -> None:
        self.path = path
        self.faults = faults
        super().__init__(f"{path} is not a valid policy: " + "; ".join(faults))


def _where(path: Any) -> str:
    return ".".join(str(step) for step in path) or "(the document)"


def door_key_faults(keys: list[Any]) -> list[str]:
    """Why an entry of `door_keys` is not an Ed25519 public key; empty when they all parse."""
    faults: list[str] = []
    for index, key in enumerate(keys):
        where = f"door_keys.{index}"
        if not isinstance(key, str):
            faults.append(f"{where}: a key is a string")
            continue
        try:
            raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
            if len(raw) != 32:
                raise ValueError("not 32 bytes")
            Ed25519PublicKey.from_public_bytes(raw)
        except (ValueError, binascii.Error):
            faults.append(
                f"{where}: not an Ed25519 public key (the unpadded base64url of its 32 raw bytes, "
                "43 characters, as the console shows it)"
            )
    return faults


def faults_of(document: Any) -> list[str]:
    """Everything wrong with a policy document, each fault under the path of its field."""
    if not isinstance(document, dict):
        return ["(the document): a policy is a mapping"]
    validator = Draft202012Validator(load_schema(POLICY_SCHEMA))
    faults: list[str] = []
    for error in sorted(validator.iter_errors(document), key=lambda e: list(map(str, e.absolute_path))):
        if error.validator == "required" and isinstance(error.instance, dict):
            # A missing field is named by its own path, not by its parent's.
            missing = [name for name in error.validator_value if name not in error.instance]
            faults += [f"{_where([*error.absolute_path, name])}: is required" for name in missing]
        else:
            faults.append(f"{_where(error.absolute_path)}: {error.message}")
    keys = document.get("door_keys")
    if isinstance(keys, list):
        faults += [fault for fault in door_key_faults(keys) if fault.split(":")[0] not in _places(faults)]
    if faults:
        return faults
    try:
        OutpostPolicy.model_validate(document)
    except ValidationError as error:
        faults = [f"{_where(item['loc'])}: {item['msg']}" for item in error.errors()]
    return faults


def _places(faults: list[str]) -> set[str]:
    return {fault.split(":")[0] for fault in faults}


def parse(text: str, path: Path) -> OutpostPolicy:
    """The policy a text holds; `PolicyError` with every fault otherwise."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise PolicyError(path, [f"(the document): not YAML ({str(error).splitlines()[0]})"]) from error
    faults = faults_of(document)
    if faults:
        raise PolicyError(path, faults)
    return OutpostPolicy.model_validate(document)


def load(path: Path) -> OutpostPolicy:
    """The policy of the file at `path`; `PolicyError` when there is none to enforce."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PolicyError(path, [f"(the file): {error.strerror or error}"]) from error
    return parse(text, path)


def allows(endpoint: OutpostEndpoint, tool: str) -> bool:
    """Whether a call may name `tool` at this endpoint: `*` stands for any run of characters, nothing
    else is special, and the whole name must match. An empty list serves none."""
    return any(
        re.fullmatch(".*".join(re.escape(part) for part in pattern.split("*")), tool, flags=re.DOTALL)
        for pattern in endpoint.tools
    )


def weight(value: Any) -> int:
    """What a payload weighs: the bytes of its compact JSON, UTF-8."""
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))


def ceiling(endpoint: OutpostEndpoint) -> int:
    """The most a call's arguments, or its result, may weigh at this endpoint, in bytes."""
    return endpoint.max_payload_kb * 1024
