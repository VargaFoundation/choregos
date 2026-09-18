"""Rejouer des historiques archivés : un changement de logique ne doit pas casser les runs en cours.

C'est la contrepartie de `workflow.patched()` : la CI rejoue les historiques enregistrés
contre le code actuel. Si un workflow prend une décision différente, le replay échoue —
avant que ça n'arrive en production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

HISTORIES = Path(__file__).parent / "histories"


def history_files() -> list[Path]:
    return sorted(HISTORIES.glob("*.json"))


@pytest.mark.skipif(not history_files(), reason="aucun historique archivé (voir `make replay-record`)")
@pytest.mark.parametrize("path", history_files(), ids=lambda path: path.stem)
async def test_replay_history(path: Path) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    payload = json.loads(path.read_text(encoding="utf-8"))
    history = WorkflowHistory.from_json(payload.get("workflowId", path.stem), payload)
    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(history)


async def test_record_and_replay_roundtrip(tmp_path: Path) -> None:
    """Enregistre un historique neuf puis le rejoue : la boucle est vérifiable en CI."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from choregos_orchestrator.workflows.train import ReleaseTrain
    from temporalio.client import WorkflowHistory
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Replayer, Worker

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with Worker(
            environment.client,
            task_queue="replay",
            workflows=[ReleaseTrain],
            activities=_stub_activities(),
        ):
            handle = await environment.client.start_workflow(
                "ReleaseTrain",
                {"project_slug": "replay", "env": "dev"},
                id="train-replay-dev",
                task_queue="replay",
            )
            await handle.signal("abort", {"by": "test"})
            await handle.result()
            history = await handle.fetch_history()
    finally:
        await environment.shutdown()

    archived = tmp_path / "train.json"
    archived.write_text(history.to_json(), encoding="utf-8")

    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(
        WorkflowHistory.from_json("train-replay-dev", json.loads(archived.read_text()))
    )


def _stub_activities() -> list[Any]:
    """Activités neutres : le replay ne rejoue pas les effets, seulement les décisions."""
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "auto_sync"}

    return [load_train_config]
