"""The artifact contract (ADR 0049): the schema, the model and the type vocabulary agree."""

from __future__ import annotations

import hashlib
import json

import pytest
import tranibor_contracts as contracts
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from tranibor_contracts import (
    ARTIFACT_TYPES,
    Artifact,
    ArtifactKind,
    artifact_digest,
    artifact_type,
    inline_bytes,
    type_of_filename,
    type_of_value,
)

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


def test_only_text_and_data_are_inline() -> None:
    inline = {name for name, entry in ARTIFACT_TYPES.items() if entry.inline}
    assert inline == {"markdown", "json", "diff", "level", "metric", "keyvalue"}
    assert artifact_type("xlsx").media_type.endswith("spreadsheetml.sheet")
    with pytest.raises(KeyError, match="unknown artifact type"):
        artifact_type("exe")


def test_a_diff_is_inline_and_its_name_is_the_platforms() -> None:
    entry = artifact_type("diff")
    assert entry.inline
    assert entry.kind is ArtifactKind.DIFF
    assert entry.media_type == "text/x-diff"
    assert contracts.DIFF_ARTIFACT in contracts.RESERVED_ARTIFACT_NAMES


def test_the_workflow_schema_knows_every_type() -> None:
    schema = contracts.load_schema("workflow.schema.json")
    transition = schema["$defs"]["transition"]["properties"]["output_types"]["additionalProperties"]
    assert set(transition["enum"]) == set(ARTIFACT_TYPES)


# ───────────────────────── files handed to a work item (ADR 0201) ─────────────────────────


def _workflow(attachments: object) -> dict[str, object]:
    return {
        "apiVersion": "choregos/v1",
        "kind": "Workflow",
        "metadata": {"name": "t", "version": 1, "attachments": attachments},
        "actors": {"owner": {"type": "human", "group": "po"}},
        "states": {"inbox": {"display": "Inbox"}, "done": {"display": "Done", "terminal": True}},
        "transitions": [{"id": "t1", "from": "inbox", "to": "done", "by": "owner"}],
    }


def _schema_errors(raw: dict[str, object]) -> list[str]:
    validator = Draft202012Validator(contracts.load_schema("workflow.schema.json"))
    return [error.message for error in validator.iter_errors(raw)]


def test_a_workflow_that_takes_files_parses_with_the_defaults() -> None:
    raw = _workflow({"accept": ["pdf", "xlsx"]})
    assert _schema_errors(raw) == []
    attachments = contracts.Workflow.model_validate(raw).metadata.attachments
    assert attachments is not None
    assert (attachments.accept, attachments.min, attachments.max) == (["pdf", "xlsx"], 0, 20)


def test_a_workflow_without_the_block_takes_no_file() -> None:
    raw = _workflow(None)
    del raw["metadata"]["attachments"]  # type: ignore[call-overload]
    assert contracts.Workflow.model_validate(raw).metadata.attachments is None


def test_min_above_max_is_refused() -> None:
    with pytest.raises(ValidationError, match="above `max`"):
        contracts.Workflow.model_validate(_workflow({"accept": ["pdf"], "min": 5, "max": 3}))


def test_an_unknown_accepted_type_is_refused_and_named() -> None:
    raw = _workflow({"accept": ["pdf", "exe"]})
    with pytest.raises(ValidationError, match="unknown artifact type `exe`"):
        contracts.Workflow.model_validate(raw)
    assert _schema_errors(raw)


@pytest.mark.parametrize(
    "block",
    [
        {"accept": []},
        {"min": 1},
        {"accept": ["pdf"], "max": 51},
        {"accept": ["pdf"], "max": 0},
        {"accept": ["pdf"], "min": -1},
        {"accept": ["pdf"], "extra": 1},
        {"accept": ["pdf"] * 21},
    ],
)
def test_the_block_is_closed_and_bounded_in_the_schema_and_the_model(block: dict[str, object]) -> None:
    raw = _workflow(block)
    assert _schema_errors(raw)
    with pytest.raises(ValidationError):
        contracts.Workflow.model_validate(raw)


def test_attachments_is_a_reserved_name() -> None:
    assert contracts.ATTACHMENTS_INPUT == "attachments"
    assert contracts.ATTACHMENTS_INPUT in contracts.RESERVED_ARTIFACT_NAMES


def test_a_handed_file_says_who_handed_it() -> None:
    assert Artifact.model_validate(_example()).handed_by is None
    assert Artifact.model_validate({**_example(), "handed_by": "person"}).handed_by == "person"
    with pytest.raises(ValidationError):
        Artifact.model_validate({**_example(), "handed_by": "robot"})
    validator = Draft202012Validator(contracts.load_schema("artifact.schema.json"))
    assert list(validator.iter_errors({**_example(), "handed_by": "a2a"})) == []
    assert list(validator.iter_errors({**_example(), "handed_by": "robot"}))
