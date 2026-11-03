"""The fields of the GitHub Projects board read in English (S22-21, ADR 0039, finding #290).

The orchestrator writes `Cost (€)`, `Size`, `Risk`; provisioning renames the French fields of a
board created earlier (`Coût (€)`, `Taille`, `Risque`) instead of creating second ones; the shipped
templates name the English fields.
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest
import yaml

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

ROOT = pathlib.Path(__file__).resolve().parents[3]


async def test_the_status_comment_writes_the_board_fields_in_english(setup: Fixture) -> None:
    from choregos_orchestrator.activities.tracker import update_status_comment

    await update_status_comment({"project_id": setup.project_id, "work_item_id": setup.work_item_id})

    written = [args for name, args in setup.adapters.tracker.calls if name == "set_fields"]
    assert len(written) == 1
    key, fields = written[0]
    assert key == setup.tracker_key
    assert set(fields) == {"Cost (€)", "Size", "Risk", "Run"}
    assert fields["Size"] == "M" and fields["Risk"] == "low"


async def test_the_board_step_renames_an_older_board_without_doubling_its_fields() -> None:
    from choregos_adapters.fakes.tracker import FakeTracker
    from choregos_orchestrator.activities.provisioning import _github_ensure_project_board

    tracker = FakeTracker()
    tracker.board_fields = ["Status", "Coût (€)", "Taille", "Risque"]  # lang: fr-ok
    bundle = SimpleNamespace(adapters=SimpleNamespace(tracker=tracker))

    message = await _github_ensure_project_board({}, bundle, None)

    assert tracker.board_fields == ["Status", "Cost (€)", "Size", "Risk", "Run"]
    assert message == (
        "board fields: 1 kept, 1 created, 3 renamed "
        "(Coût (€) → Cost (€); Taille → Size; Risque → Risk)"  # lang: fr-ok
    )
    # Replayable (ADR 0008): a second pass finds nothing left to do.
    assert (
        await _github_ensure_project_board({}, bundle, None) == "board fields: 5 kept, 0 created, 0 renamed"
    )
    assert tracker.board_fields == ["Status", "Cost (€)", "Size", "Risk", "Run"]


@pytest.mark.parametrize("template", ["github-tekton-argo-k8s", "github-aca"])
async def test_the_shipped_templates_name_the_english_fields(template: str) -> None:
    manifest = yaml.safe_load((ROOT / "templates" / template / "manifest.yaml").read_text(encoding="utf-8"))
    board_steps = [
        p["github.ensure_project_board"]
        for p in manifest["steps"]
        if isinstance(p, dict) and "github.ensure_project_board" in p
    ]
    assert board_steps == [{"fields": ["Status", "Cost (€)", "Size", "Risk", "Run"]}]


async def test_without_a_board_number_the_step_says_so_instead_of_counting_zero() -> None:
    from choregos_orchestrator.activities.provisioning import _github_ensure_project_board

    async def ensure_project_fields(fields: list[str]) -> dict[str, list[str]]:
        return {"kept": [], "renamed": [], "created": []}

    bundle = SimpleNamespace(
        adapters=SimpleNamespace(tracker=SimpleNamespace(ensure_project_fields=ensure_project_fields))
    )
    message = await _github_ensure_project_board({}, bundle, None)
    assert message == "no Projects v2 board configured: give its number to the connector"
