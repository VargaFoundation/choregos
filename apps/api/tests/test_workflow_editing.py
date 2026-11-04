"""`POST /workflows/edit`: typed operations, grafted into the text (ADR 0031, S16-11).

The console edits a workflow from the map without rewriting its YAML: the response carries the
edited text, its diff, the validation, the graph and the inverse, which gives back the original
bytes.
"""

from __future__ import annotations

from typing import Any

from choregos_core.dsl import template_yaml
from httpx import AsyncClient

ORIGIN = template_yaml("default-simple")


async def _edit(client: AsyncClient, text: str, *operations: dict[str, Any]) -> Any:
    return await client.post("/api/v1/workflows/edit", json={"yaml": text, "operations": list(operations)})


async def test_an_added_guarantee_reads_back_as_a_diff_and_can_be_undone(
    client: AsyncClient, admin: str
) -> None:
    response = await _edit(
        client, ORIGIN, {"op": "add_gate", "transition": "t-implement", "gate": "ci_green", "index": 0}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True and body["graph"]["nodes"]
    # The process view of the edited text (S16-08): the added guarantee is stated there.
    (step,) = [e for e in body["process"] if e["id"] == "t-implement"]
    assert "ci_green" in {g["name"] for g in step["gates"]}
    changed = [
        line
        for line in body["diff"].splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    removed, added = changed  # one line changes, the rest does not move
    assert removed.startswith("-") and "ci_green" not in removed
    assert added.startswith("+") and "gates: [ci_green, " in added
    assert [c for c in ORIGIN.splitlines() if c.lstrip().startswith("#")] == [
        c for c in body["yaml"].splitlines() if c.lstrip().startswith("#")
    ], "the comments stay"
    undone = await client.post(
        "/api/v1/workflows/edit", json={"yaml": body["yaml"], "operations": body["inverse"]}
    )
    assert undone.json()["yaml"] == ORIGIN


async def test_renaming_a_state_with_an_effect_writes_it_first(client: AsyncClient, admin: str) -> None:
    """A workflow written before #175: `pr_open` opens the PR by its NAME. Renamed, the effect is
    written first (`does: open_pr`) and the response says so; the inverse gives back the original text."""
    old = ORIGIN.replace("    does: open_pr\n", "")
    assert "does: open_pr" not in old
    response = await _edit(client, old, {"op": "rename_state", "from": "pr_open", "to": "review"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "does: open_pr" in body["yaml"] and "pr_open" not in body["yaml"]
    assert any("is now written" in n for n in body["notices"])
    undone = await client.post(
        "/api/v1/workflows/edit", json={"yaml": body["yaml"], "operations": body["inverse"]}
    )
    assert undone.json()["yaml"] == old


async def test_what_does_not_apply_receives_422(client: AsyncClient, admin: str) -> None:
    unknown = await _edit(client, ORIGIN, {"op": "remove_transition", "id": "t-unknown"})
    assert unknown.status_code == 422 and "t-unknown" in unknown.text
    malformed = await _edit(client, ORIGIN, {"op": "fly_to_the_moon"})
    assert malformed.status_code == 422, malformed.text
    no_operation = await client.post("/api/v1/workflows/edit", json={"yaml": ORIGIN, "operations": []})
    assert no_operation.status_code == 422


async def test_set_workflow_and_a_block_value_go_through_the_api_and_come_back(
    client: AsyncClient, admin: str
) -> None:
    """ADR 0112 §3: the workflow's own fields, and a human task written as lines."""
    text = template_yaml("default-request")
    response = await _edit(
        client,
        text,
        {"op": "set_workflow", "field": "description", "value": "Requests of the HR desk."},
        {
            "op": "set_workflow",
            "field": "inputs",
            "value": {"type": "object", "properties": {"verdict": {"type": "string"}}},
            "style": "block",
        },
        {
            "op": "set_transition",
            "id": "t-accept",
            "field": "task",
            "value": {
                "title": "Accept the deliverable",
                "form": {"type": "object", "properties": {"verdict": {"type": "string"}}},
                "attest": "I read it.",
            },
            "style": "block",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True
    assert "    task:\n      title: Accept the deliverable\n" in body["yaml"]
    undone = await _edit(client, body["yaml"], *body["inverse"])
    assert undone.json()["yaml"] == text
