"""An artifact says how it is shown (ADR 0129): types, scales, what a value comes to."""

from __future__ import annotations

from typing import Any

import choregos_contracts as contracts
import pytest
from choregos_contracts import (
    ARTIFACT_TYPES,
    SCALES,
    Artifact,
    ArtifactDisplay,
    ArtifactKind,
    ArtifactLevel,
    ArtifactSpec,
    StageInput,
    Workflow,
    coerce_value,
    resolve_display,
)
from jsonschema import Draft202012Validator
from pydantic import ValidationError

SIZE = resolve_display("size")
assert SIZE is not None


def test_value_types_are_inline_and_a_level_is_plain_text() -> None:
    assert ARTIFACT_TYPES["level"].kind is ArtifactKind.VALUE
    assert ARTIFACT_TYPES["metric"].inline and ARTIFACT_TYPES["keyvalue"].inline
    assert ARTIFACT_TYPES["level"].media_type == "text/plain"


def test_risk_size_and_verdict_are_scales_not_types() -> None:
    assert "risk" not in ARTIFACT_TYPES and "size" not in ARTIFACT_TYPES
    assert [level.value for level in SCALES["risk"]] == ["low", "medium", "high"]
    assert [level.tone for level in SCALES["risk"]] == ["ok", "warn", "danger"]
    assert {level.tone for level in SCALES["size"]} == {"neutral"}, "a size only ranks"


@pytest.mark.parametrize("name", ["size", "risk", "verdict", "confidence", "diff"])
def test_the_platform_knows_its_own_outputs_by_name(name: str) -> None:
    assert resolve_display(name) is not None


def test_a_name_nobody_speaks_of_has_no_display() -> None:
    assert resolve_display("spec_markdown") is None


def test_a_declaration_is_laid_over_what_the_platform_knows() -> None:
    display = resolve_display("verdict", ArtifactSpec(show="facts", title="Review"))
    assert display is not None
    assert (display.type, display.show, display.title) == ("level", "facts", "Review")
    assert display.levels == SCALES["verdict"]


def test_a_workflow_scale_replaces_nothing_it_does_not_name() -> None:
    levels = [ArtifactLevel(value="sev3", tone="warn"), ArtifactLevel(value="sev1", tone="danger")]
    display = resolve_display("severity", ArtifactSpec(type="level", levels=levels, show="facts"))
    assert display is not None
    assert display.levels == levels


def test_a_place_can_be_given_without_saying_what_the_value_is() -> None:
    display = resolve_display("notes", ArtifactSpec(show="hidden", title="Notes"))
    assert display is not None
    assert (display.type, display.show, display.title) == (None, "hidden", "Notes")


@pytest.mark.parametrize(("raw", "expected"), [("M", "M"), (" m ", "M"), ("xl", "XL")])
def test_a_level_comes_to_the_word_of_its_scale(raw: str, expected: str) -> None:
    assert SIZE is not None and coerce_value(SIZE, raw) == expected


@pytest.mark.parametrize("raw", ["XXL", "", True, None, ["S"]])
def test_a_value_off_its_scale_is_refused_and_named(raw: Any) -> None:
    with pytest.raises(ValueError, match=r"one of|one word"):
        assert SIZE is not None
        coerce_value(SIZE, raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(87, 87), (0.5, 0.5), ("87%", 87), (" 42 % ", 42), ("0.9", 0.9), ("12 ms", 12), ("-3", -3)],
)
def test_a_metric_comes_to_a_number(raw: Any, expected: float) -> None:
    assert coerce_value(ArtifactDisplay(type="metric", unit="ms"), raw) == expected


@pytest.mark.parametrize("raw", ["high", True, float("nan"), float("inf"), [1], None])
def test_what_is_not_a_finite_number_is_no_metric(raw: Any) -> None:
    with pytest.raises(ValueError, match="number"):
        coerce_value(ArtifactDisplay(type="metric"), raw)


@pytest.mark.parametrize("raw", [-1, 101, "150%"])
def test_a_metric_out_of_its_bounds_is_refused(raw: Any) -> None:
    with pytest.raises(ValueError, match=r"at (least|most)"):
        coerce_value(ArtifactDisplay(type="metric", min=0, max=100), raw)


def test_a_keyvalue_is_a_flat_object_even_as_text() -> None:
    display = ArtifactDisplay(type="keyvalue")
    assert coerce_value(display, {"a": 1, "b": "x"}) == {"a": 1, "b": "x"}
    assert coerce_value(display, '{"a": true}') == {"a": True}
    for bad in ({"a": {"b": 1}}, {"a": [1]}, "text", {}, [1]):
        with pytest.raises(ValueError, match="key/value"):
            coerce_value(display, bad)


def test_a_format_is_left_as_it_came() -> None:
    assert coerce_value(ArtifactDisplay(type="diff"), "text") == "text"


def test_a_tone_is_a_word_of_a_closed_list_never_a_colour() -> None:
    with pytest.raises(ValidationError):
        ArtifactLevel(value="x", tone="#ff0000")  # type: ignore[arg-type]


def test_the_artifact_schema_and_the_model_agree_on_a_display() -> None:
    schema = contracts.load_schema("artifact.schema.json")
    assert set(schema["$defs"]["display"]["properties"]) == set(ArtifactDisplay.model_fields)
    assert set(schema["properties"]) == set(Artifact.model_fields)


def test_the_workflow_schema_and_the_model_agree_on_a_declaration() -> None:
    schema = contracts.load_schema("workflow.schema.json")
    assert set(schema["$defs"]["artifactSpec"]["properties"]) == set(ArtifactSpec.model_fields)
    assert "artifacts" in schema["properties"] and "artifacts" in Workflow.model_fields


def test_the_workflow_schema_accepts_what_the_model_accepts() -> None:
    schema = contracts.load_schema("workflow.schema.json")
    spec = {"type": "level", "levels": [{"value": "a", "tone": "ok"}, {"value": "b"}], "show": "facts"}
    errors = list(Draft202012Validator(schema["$defs"]["artifactSpec"]).iter_errors(spec))
    assert not errors, [e.message for e in errors]
    ArtifactSpec.model_validate(spec)


def test_the_stage_input_schema_knows_the_displays() -> None:
    schema = contracts.load_schema("stage-input.schema.json")
    properties = schema["properties"]["transition"]["properties"]
    assert "artifact_displays" in properties
    assert "artifact_displays" in StageInput.model_fields["transition"].annotation.model_fields  # type: ignore[union-attr]


def test_an_enormous_number_is_refused_not_a_crash() -> None:
    with pytest.raises(ValueError, match="finite number"):
        coerce_value(ArtifactDisplay(type="metric"), "1" + "0" * 400)


def test_a_whole_float_is_an_integer_so_it_hashes_the_same_after_jsonb() -> None:
    value = coerce_value(ArtifactDisplay(type="metric"), "1e22")
    assert isinstance(value, int)
    assert contracts.inline_bytes(value) == b"10000000000000000000000"
    assert isinstance(coerce_value(ArtifactDisplay(type="metric"), 2.0), int)
    assert coerce_value(ArtifactDisplay(type="metric"), 0.5) == 0.5
