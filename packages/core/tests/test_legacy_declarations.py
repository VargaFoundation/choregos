"""Projects that stored a copy of an old core template keep working (S31-02).

The core templates declared outputs nothing produces (`spec`, `plan`, `branch`, `commits`,
`duplicate_of`, `allowed_paths`). Every declared output being required now, an untouched copy is
upgraded to the fixed declarations, and a modified copy keeps those legacy outputs not required.
"""

from __future__ import annotations

import pytest
from choregos_contracts import StageResult, StageStatus, Workflow
from choregos_core import WorkflowEngine, parse_workflow
from choregos_core.dsl import template_yaml
from choregos_core.dsl.legacy import (
    OLD_DECLARATIONS,
    legacy_declarations,
    old_template,
    upgrade_core_copy,
)

#: How the old `default-simple` was written, line by line: what the fix replaced.
OLD_LINES = [
    ("    outputs: [spec_markdown, size, risk]\n", "    outputs: [spec, allowed_paths, size, risk]\n"),
    (
        "    inputs: [spec_markdown, allowed_paths]\n",
        "    inputs: [spec, allowed_paths]\n    outputs: [branch, commits]\n",
    ),
]


def _old_default_simple_yaml() -> str:
    text = template_yaml("default-simple")
    for new, old in OLD_LINES:
        assert text.count(new) == 1
        text = text.replace(new, old)
    return text


def _done(outputs: dict[str, object]) -> StageResult:
    return StageResult(status=StageStatus.DONE, summary="spec written", outputs=outputs)


@pytest.mark.parametrize("name", sorted(OLD_DECLARATIONS))
def test_an_untouched_copy_is_upgraded_to_the_fixed_declarations(name: str) -> None:
    from choregos_core import load_template

    old = old_template(name)
    old["metadata"] = {"name": "default", "version": 4}
    upgraded = upgrade_core_copy(old, "")
    assert upgraded is not None
    text, new, template = upgraded
    assert template == name
    expected = load_template(name).model_dump(mode="json", by_alias=True, exclude_none=True)
    assert {k: v for k, v in new.items() if k != "metadata"} == {
        k: v for k, v in expected.items() if k != "metadata"
    }
    assert new["metadata"] == {"name": "default", "version": 4}, "the copy keeps its own name and version"
    assert Workflow.model_validate(new)
    assert legacy_declarations(new) == []
    # The YAML could not be patched line by line (none was given): it is regenerated, and parses.
    parsed, report = parse_workflow(text, strict=False)
    assert report.valid and parsed.model_dump(mode="json", by_alias=True, exclude_none=True) == new


def test_the_yaml_of_an_untouched_copy_keeps_its_comments() -> None:
    old_text = _old_default_simple_yaml()
    old_doc, _ = parse_workflow(old_text, strict=False)
    upgraded = upgrade_core_copy(old_doc.model_dump(mode="json", by_alias=True, exclude_none=True), old_text)
    assert upgraded is not None
    text, _, _ = upgraded
    assert text == template_yaml("default-simple"), "only the declaration lines changed"


def test_a_modified_copy_is_left_alone_and_its_legacy_declarations_are_named() -> None:
    modified = old_template("default-simple")
    modified["transitions"][0]["gates"] = [{"name": "outputs_present", "params": {"allow_empty": True}}]
    assert upgrade_core_copy(modified, "") is None
    assert legacy_declarations(modified) == [
        "t-refine: spec",
        "t-refine: allowed_paths",
        "t-implement: branch",
        "t-implement: commits",
    ]


def test_a_modified_old_copy_does_not_start_failing_its_refine_step() -> None:
    """Before S31-02 the engine required nothing; now it requires what is produced, never the
    legacy declarations nobody produced."""
    engine = WorkflowEngine(Workflow.model_validate(old_template("default-simple")))
    refine = engine.select_transition("inbox")
    assert refine is not None and refine.id == "t-refine"
    assert engine.after_stage(refine, _done({"size": "M", "risk": "low"}), attempts=0).next_state != "inbox"
    # What the old template declared and agents do produce is still required.
    assert (
        "missing declared outputs: risk"
        in engine.after_stage(refine, _done({"size": "M"}), attempts=0).reason
    )


def test_the_validator_warns_about_a_legacy_declaration() -> None:
    _, report = parse_workflow(_old_default_simple_yaml(), strict=False)
    assert report.valid
    codes = [
        (issue.code, issue.message.split("`")[1])
        for issue in report.warnings
        if issue.code == "outputs.legacy"
    ]
    assert codes == [
        ("outputs.legacy", "spec"),
        ("outputs.legacy", "allowed_paths"),
        ("outputs.legacy", "branch"),
        ("outputs.legacy", "commits"),
    ]
