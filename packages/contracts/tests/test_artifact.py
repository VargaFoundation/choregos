"""The artifact contract (ADR 0049): the schema, the model and the type vocabulary agree."""

from __future__ import annotations

import hashlib
import json

import choregos_contracts as contracts
import pytest
from choregos_contracts import (
    ARTIFACT_TYPES,
    Artifact,
    ArtifactKind,
    artifact_digest,
    artifact_type,
    inline_bytes,
    type_of_filename,
    type_of_value,
)
from jsonschema import Draft202012Validator
from pydantic import ValidationError

EXAMPLE = contracts.schemas_dir() / "examples" / "artifact.example.json"


def _example() -> dict[str, object]:
    data: dict[str, object] = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return data


def test_the_example_follows_the_schema_and_the_model() -> None:
    schema = contracts.load_schema("artifact.schema.json")
    errors = list(Draft202012Validator(schema).iter_errors(_example()))
    assert not errors, [e.message for e in errors]
    model = Artifact.model_validate(_example())
    dumped = model.model_dump(mode="json", exclude_none=True)
    assert set(_example()) <= set(dumped)


def test_the_schema_and_the_model_declare_the_same_fields() -> None:
    schema = contracts.load_schema("artifact.schema.json")
    assert set(schema["properties"]) == set(Artifact.model_fields)
    producer = schema["properties"]["produced_by"]["properties"]
    assert set(producer) == set(contracts.ArtifactProducer.model_fields)


def test_the_schema_knows_every_type_and_kind() -> None:
    schema = contracts.load_schema("artifact.schema.json")
    assert set(schema["properties"]["type"]["enum"]) == set(ARTIFACT_TYPES)
    assert set(schema["properties"]["kind"]["enum"]) == {k.value for k in ArtifactKind}


@pytest.mark.parametrize("name", ["../etc", ".hidden", "a/b", "", "x" * 129])
def test_a_name_that_cannot_be_a_file_name_is_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        Artifact.model_validate({**_example(), "name": name})


def test_a_digest_is_sha256_of_the_bytes() -> None:
    data = b"PK\x03\x04 a deck"
    assert artifact_digest(data) == "sha256:" + hashlib.sha256(data).hexdigest()
    with pytest.raises(ValidationError):
        Artifact.model_validate({**_example(), "digest": "md5:abc"})


def test_inline_bytes_are_text_for_markdown_and_canonical_json_otherwise() -> None:
    assert inline_bytes("# Spec\n") == b"# Spec\n"
    assert inline_bytes({"b": 1, "a": [1, "é"]}) == '{"a":[1,"é"],"b":1}'.encode()
    assert type_of_value("text") == "markdown"
    assert type_of_value([{"name": "x"}]) == "json"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("deck.pptx", "pptx"),
        ("budget.XLSX", "xlsx"),
        ("photo.jpg", "jpeg"),
        ("demo.mp4", "mp4"),
        ("findings.sarif", "sarif"),
        ("notes.md", "markdown"),
        ("archive.tar.zst", "file"),
        ("noextension", "file"),
    ],
)
def test_the_type_of_a_file_comes_from_its_extension(filename: str, expected: str) -> None:
    assert type_of_filename(filename) == expected


def test_only_markdown_and_json_are_inline() -> None:
    inline = {name for name, entry in ARTIFACT_TYPES.items() if entry.inline}
    assert inline == {"markdown", "json"}
    assert artifact_type("xlsx").media_type.endswith("spreadsheetml.sheet")
    with pytest.raises(KeyError, match="unknown artifact type"):
        artifact_type("exe")
