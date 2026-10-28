"""The French names on the wire are read under both spellings (S45-03, ADR 0098 §4).

Payload keys and error types born French are recorded in Temporal histories and persisted in rows:
every reader accepts the English spelling and the former one, before any writer switches (S45-39).
What is sent, returned and persisted does not change here.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import timedelta
from typing import Any

import pytest

from .conftest import Fixture

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "src" / "choregos_orchestrator"


def test_read_key_prefers_english_then_former() -> None:
    from choregos_orchestrator.wire_names import read_key

    assert read_key({"effets": 2}, "effects") == 2  # lang: fr-ok
    assert read_key({"effects": 3, "effets": 2}, "effects") == 3  # lang: fr-ok
    assert read_key({}, "effects") is None
    assert read_key({}, "effects", 0) == 0
    # A present English key wins even when it is false: `waiting: False` is not "absent".
    assert read_key({"waiting": False, "en_attente": True}, "waiting") is False  # lang: fr-ok
    with pytest.raises(KeyError):
        read_key({"effects": 1}, "effect")  # a typo reads nothing, loudly


def _direct_reads(path: pathlib.Path, former: set[str]) -> list[str]:
    """`x.get('<former>')` and `x['<former>']` (read, not stored) in one module."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        key: object = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            key = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            key = node.slice.value if isinstance(node.slice, ast.Constant) else None
        if isinstance(key, str) and key in former:
            found.append(f"{path.relative_to(PACKAGE)}:{node.lineno} reads {key!r}")
    return found


def test_no_workflow_or_activity_reads_a_former_key_directly() -> None:
    """A recorded or persisted key is read through `read_key`, which accepts both spellings: a
    direct read of the French one would miss what S45-39 writes in English."""
    from choregos_orchestrator.wire_names import FORMER_PAYLOAD_KEYS

    former = set(FORMER_PAYLOAD_KEYS.values())
    modules = sorted([*PACKAGE.glob("workflows/*.py"), *PACKAGE.glob("activities/*.py")])
    assert len(modules) > 20, "the scan reads nothing"
    direct = [read for path in modules for read in _direct_reads(path, former)]
    assert not direct, direct


def test_configuration_refusals_accept_both_names() -> None:
    from choregos_orchestrator.workflows.interpreter import CONFIGURATION_REFUSALS

    english = {"AgentUnavailable", "InvalidInstructions", "SkillMissing"}
    former = {"AgentIndisponible", "InstructionsInvalides", "SkillAbsente"}  # lang: fr-ok
    others = {"ModelResolutionError", "SkillPendingReview", "KnowledgeRefused", "BackendDisabled"}
    assert english | former | others == CONFIGURATION_REFUSALS


def test_effect_retry_policy_names_both() -> None:
    from choregos_orchestrator.workflows.action import EFFET

    expected = {"EffectRefused", "EffetRefuse", "ActionNotFound", "ActionIntrouvable"}  # lang: fr-ok
    assert set(EFFET.non_retryable_error_types or []) == expected


async def _action(setup: Fixture) -> str:
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        action = Action(
            org_id=project.org_id,
            project_id=project.id,
            origin="ontology",
            kind="ontology.verify_finding_fixed",
            title="A fix, and the evidence that it served",
            params={},
            effects=[{"effect": "check", "with": {}}, {"effect": "check", "with": {}}],
            proposed_by={"kind": "system", "id": "system"},
            approval={},
            decisions=[],
            status="running",
        )
        session.add(action)
        await session.flush()
        return str(action.id)


async def _row(action_id: str) -> Any:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return await session.get(Action, action_id)


@pytest.mark.parametrize(
    ("waiting", "until", "evidence"),
    [("waiting", "until", "evidence"), ("en_attente", "jusqu_a", "preuve")],  # lang: fr-ok
    ids=["english", "former"],
)
async def test_awaiting_evidence_reads_english_and_former_keys(
    setup: Fixture, waiting: str, until: str, evidence: str
) -> None:
    from choregos_core import utcnow
    from choregos_orchestrator.activities.actions import mark_awaiting_evidence

    action_id = await _action(setup)
    deadline = (utcnow() + timedelta(hours=1)).isoformat()
    await mark_awaiting_evidence({"action_id": action_id, "position": 1, waiting: True, until: deadline})
    row = await _row(action_id)
    assert row.status == "awaiting_evidence"
    # What is persisted does not change with the spelling read (S45-39 switches the writers).
    assert row.result["attente"] == {"position": 1, "jusqu_a": deadline}  # lang: fr-ok
    seen = {"position": 1, "ok": True, "detail": "the next report no longer has the key"}
    await mark_awaiting_evidence({"action_id": action_id, "position": 1, waiting: False, evidence: seen})
    row = await _row(action_id)
    assert row.status == "running" and "attente" not in row.result  # lang: fr-ok
    assert row.result["preuves"] == [seen]  # lang: fr-ok


async def _migration_events(setup: Fixture) -> list[Any]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from sqlalchemy import select

    types = (str(EventType.WORKITEM_MIGRATED), str(EventType.WORKITEM_MIGRATION_REFUSED))
    async with session_scope() as session:
        rows = await session.execute(
            select(Event).where(Event.work_item_id == setup.work_item_id, Event.type.in_(types))
        )
        return list(rows.scalars())


async def test_migration_record_is_idempotent_across_key_names(setup: Fixture) -> None:
    """A replayed `record_migration` (ADR 0008) finds the key in the events already persisted,
    whichever spelling the payload uses."""
    from choregos_orchestrator.activities.interpretation import record_migration

    key = "wi-x/run-1/1"
    common = {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "from_state": "inbox"}
    former = {**common, "cle": key, "refus": "the state moved since the check"}  # lang: fr-ok
    assert (await record_migration(former)) == {"recorded": True, "pinned": False}
    english = {**common, "key": key, "refusal": "the state moved since the check"}
    assert (await record_migration(english)) == {"recorded": False}
    events = await _migration_events(setup)
    assert len(events) == 1
    assert events[0].payload["cle"] == key, "the event still persists the former key"  # lang: fr-ok


async def test_migration_refusal_is_read_under_the_english_key(setup: Fixture) -> None:
    from choregos_contracts import EventType
    from choregos_orchestrator.activities.interpretation import record_migration

    payload = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "from_state": "inbox",
        "key": "wi-y/run-1/1",
        "refusal": "the state moved since the check",
    }
    assert (await record_migration(payload)) == {"recorded": True, "pinned": False}
    events = await _migration_events(setup)
    assert [(e.type, e.payload["reason"]) for e in events] == [
        (str(EventType.WORKITEM_MIGRATION_REFUSED), "the state moved since the check")
    ]
    assert events[0].payload["cle"] == "wi-y/run-1/1"  # lang: fr-ok


def test_train_input_decodes_with_and_without_relay_threshold() -> None:
    """A continue-as-new input recorded before S45-03 names the threshold `seuil_de_relais`; one
    written after S45-39 names it `relay_threshold`. Both decode, to the same threshold."""
    from choregos_orchestrator.workflows.train import SEUIL_DE_RELAIS, TrainInput
    from temporalio.converter import DataConverter

    converter = DataConverter.default.payload_converter
    base = {"project_slug": "billing-api", "env": "prod"}
    former = {**base, "seuil_de_relais": 40}  # lang: fr-ok
    english = {**base, "relay_threshold": 40}

    def as_dataclass(shape: dict[str, Any]) -> Any:
        return converter.from_payloads(converter.to_payloads([shape]), [TrainInput])[0]

    def as_the_workflow_reads_it(shape: dict[str, Any]) -> Any:
        # `ReleaseTrain.run` takes a dict and builds its input itself: `TrainInput(**payload)`.
        return TrainInput(**converter.from_payloads(converter.to_payloads([shape]))[0])

    for decode in (as_dataclass, as_the_workflow_reads_it):
        old, new = decode(former), decode(english)
        assert (old.relay_threshold, new.relay_threshold) == (None, 40)
        assert old.threshold() == new.threshold() == 40
        assert old.seuil_au_repos_d_avant() == new.seuil_au_repos_d_avant() == 40
        assert old.seuil() == old.threshold(), "the former name delegates"
    unset = as_dataclass(base)
    assert unset.threshold() == SEUIL_DE_RELAIS
