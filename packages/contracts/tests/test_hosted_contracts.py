"""Hosted and prompt steps in the stage input (ADR 0080): one kind of step at a time, images by digest."""

from __future__ import annotations

import copy
import json
from typing import Any

import choregos_contracts as contracts
import pytest
import yaml
from choregos_contracts import HostedCall, StageInput
from jsonschema import Draft202012Validator, RefResolver
from pydantic import ValidationError

EXAMPLES = contracts.schemas_dir() / "examples"
OPENAPI = contracts.schemas_dir().parent / "openapi.yaml"


def _validator() -> Draft202012Validator:
    schema = contracts.load_schema("stage-input.schema.json")
    store = {}
    for name in contracts.SCHEMA_FILES:
        sub = contracts.load_schema(name)
        store[sub["$id"]] = sub
        store[name] = sub
    return Draft202012Validator(schema, resolver=RefResolver(schema["$id"], schema, store=store))


def _example(name: str) -> dict[str, Any]:
    with (EXAMPLES / name).open(encoding="utf-8") as fh:
        loaded: dict[str, Any] = json.load(fh)
    return loaded


def _both_refuse(payload: dict[str, Any]) -> None:
    assert list(_validator().iter_errors(payload)), "the schema accepted it"
    with pytest.raises(ValidationError):
        StageInput.model_validate(payload)


def test_a_stage_input_without_the_new_fields_validates_as_before() -> None:
    old = _example("stage-input.example.json")
    assert "hosted" not in old and "prompt" not in old
    assert not list(_validator().iter_errors(old))
    parsed = StageInput.model_validate(old)
    assert parsed.hosted is None and parsed.prompt is None


def test_a_hosted_step_and_a_remote_call_together_are_refused() -> None:
    both = _example("stage-input.hosted.example.json")
    both["remote"] = _example("stage-input.remote.example.json")["remote"]
    with pytest.raises(ValidationError, match="carried by one thing"):
        StageInput.model_validate(both)


def test_a_prompt_step_and_a_hosted_step_together_are_refused() -> None:
    both = _example("stage-input.hosted.example.json")
    both["prompt"] = _example("stage-input.prompt.example.json")["prompt"]
    with pytest.raises(ValidationError, match="carried by one thing"):
        StageInput.model_validate(both)


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/acme/billing-triager:latest",
        "ghcr.io/acme/billing-triager",
        "ghcr.io/acme/billing-triager@sha256:" + "1" * 63,
    ],
)
def test_a_hosted_image_not_pinned_to_a_digest_is_refused(image: str) -> None:
    payload = copy.deepcopy(_example("stage-input.hosted.example.json"))
    payload["hosted"]["image"] = image
    _both_refuse(payload)


def test_a_hosted_port_on_a_loopback_listener_is_refused() -> None:
    payload = _example("stage-input.hosted.example.json")
    for port in (4001, 4002, 4003):
        with pytest.raises(ValidationError, match="loopback"):
            HostedCall.model_validate({**payload["hosted"], "port": port})
        _both_refuse({**payload, "hosted": {**payload["hosted"], "port": port}})


def test_the_registry_api_names_the_three_runtimes() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    runtime = schemas["AgentSpec"]["properties"]["runtime"]
    mapping = runtime["discriminator"]["mapping"]
    for kind, schema in (("prompt", "PromptRuntime"), ("voice", "VoiceRuntime"), ("hosted", "HostedRuntime")):
        assert mapping[kind] == f"#/components/schemas/{schema}"
        assert schema in schemas
    source = schemas["HostedRuntime"]["properties"]["source"]
    assert set(source["discriminator"]["mapping"]) == {"image", "zip"}
