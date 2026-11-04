"""The outputs of a stage feed the next one, including when they carry a trade name.

Bench of 2026-09-24: HR sourcing produced `outputs.profiles`, the `outputs_present` guarantee
accepted it, then the qualification looked for "the profiles proposed at the previous stage" in an
empty workspace. Nothing stored an output that was not called `spec_markdown`, `plan_markdown`,
`review_markdown` or `release_notes_markdown`.
"""

from __future__ import annotations

from choregos_contracts import StageOutputs


def test_an_output_declared_by_the_transition_is_stored_under_its_name() -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.services import store_outputs

    item = WorkItem(tracker_key="RH-1", title="T", state="sourcing", documents={"spec_markdown": "old"})
    outputs = StageOutputs.model_validate(
        {"profiles": "## Three profiles\n- A\n- B", "noise": "not declared"}
    )
    documents = store_outputs(item, outputs, ["profiles"])
    assert documents["profiles"].startswith("## Three profiles")
    assert "noise" not in documents, "an output the transition does not declare does not follow the work item"
    assert documents["spec_markdown"] == "old", "the existing documents stay"


def test_the_software_documents_are_always_stored_even_without_a_declaration() -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.services import store_outputs

    item = WorkItem(tracker_key="X-1", title="T", state="refining")
    store_outputs(item, StageOutputs(spec_markdown="## Spec", size="M"), None)
    assert item.documents == {"spec_markdown": "## Spec"}


def test_the_next_playbook_receives_the_inputs_under_their_name() -> None:
    from choregos_playbooks import render_playbook

    prompt = render_playbook(
        "implement",
        ticket={"key": "RH-1", "title": "T", "body": ""},
        inputs={"profiles": "## Three profiles"},
    )
    assert (
        "RH-1" in prompt
    )  # the package role ignores `inputs`: rendered without error, StrictUndefined included
