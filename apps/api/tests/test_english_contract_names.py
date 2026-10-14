"""The four access properties and the two issue codes renamed to English (ADR 0046, S34-03).

For one release the API emits both names: the English one, and the former French one that the
contract marks `deprecated`. These tests read the responses against `openapi.yaml`, so that the
contract and what is served cannot drift apart while both names live.
"""

from __future__ import annotations

from typing import Any

import choregos_contracts as contracts
from httpx import AsyncClient
from jsonschema import Draft202012Validator

#: English name -> former name, per object of the access report.
RENAMED_ACCESS_REPORT = {"refusals": "refus", "tool_cost_eur": "cout_outils_eur"}  # lang: fr-ok
RENAMED_ACCESS_LINE = {"target": "cible", "requests": "demandes", "refusals": "refus"}  # lang: fr-ok

WORKFLOW_WITH_BOTH_CODES = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: t, version: 1 }
actors:
  dev: { type: agent, role: implement }
  ci: { type: system }
states:
  inbox: { display: Inbox }
  doing: { display: Doing }
  pr_open: { display: Pull request }
  done: { display: Done, terminal: true }
transitions:
  - { id: t1, from: inbox, to: doing, by: dev, gates: [outputs_present],
      on_fail: { to: inbox, max_attempts: 2, escalate_to: done } }
  - { id: t2, from: doing, to: pr_open, by: ci }
  - { id: t3, from: pr_open, to: done, by: ci }
"""


def _access_schema() -> dict[str, Any]:
    spec = contracts.load_openapi()
    operation = spec["paths"]["/runs/{id}/access"]["get"]
    schema: dict[str, Any] = operation["responses"]["200"]["content"]["application/json"]["schema"]
    return schema


def _undeclared(value: Any, schema: dict[str, Any], where: str = "") -> list[str]:
    """Every key the API emits must be declared by the contract, nested objects included."""
    if isinstance(value, list):
        items = schema.get("items", {})
        return [miss for i, v in enumerate(value) for miss in _undeclared(v, items, f"{where}[{i}]")]
    if not isinstance(value, dict):
        return []
    declared = schema.get("properties", {})
    missing = [f"{where}.{key}" for key in value if key not in declared]
    for key, sub in value.items():
        if key in declared:
            missing += _undeclared(sub, declared[key], f"{where}.{key}")
    return missing


async def _run_with_access(project: dict[str, Any]) -> str:
    from choregos_api.db.models import CostLedger, Run, RunEvent, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#34", title="T", state="ready")
        session.add(item)
        await session.flush()
        run = Run(
            id="run-s34-03",
            work_item_id=item.id,
            project_id=project["id"],
            stage_role="implement",
            status="succeeded",
        )
        session.add(run)
        await session.flush()
        events = [
            {"kind": "read", "target": "/workspace/src/a.py", "allowed": True, "reason": "ok"},
            {"kind": "read", "target": "/workspace/src/a.py", "allowed": True, "reason": "ok"},
            {"kind": "write", "target": "/workspace/.env", "allowed": False, "reason": "sensitive file"},
        ]
        for seq, payload in enumerate(events, start=1):
            session.add(
                RunEvent(
                    run_id=run.id, seq=seq, type="session/request_permission", payload=payload, ts=utcnow()
                )
            )
        session.add(
            CostLedger(
                project_id=project["id"],
                work_item_id=item.id,
                run_id=run.id,
                kind="tool",
                provider="address",
                model="check_address",
                cost_eur=0.002,
                ts=utcnow(),
            )
        )
    return "run-s34-03"


async def test_access_report_emits_english_and_former_names(
    client: AsyncClient, project: dict[str, Any], admin: str
) -> None:
    run_id = await _run_with_access(project)
    response = await client.get(f"/api/v1/runs/{run_id}/access")
    assert response.status_code == 200, response.text
    report = response.json()

    assert report["refusals"] == 1 and report["tool_cost_eur"] == 0.002
    for english, former in RENAMED_ACCESS_REPORT.items():
        assert report[former] == report[english], former
    denied = report["acces"][0]
    assert (denied["target"], denied["requests"], denied["refusals"]) == ("/workspace/.env", 1, 1)
    read = next(line for line in report["acces"] if line["target"] == "/workspace/src/a.py")
    assert read["requests"] == 2
    for line in report["acces"]:
        for english, former in RENAMED_ACCESS_LINE.items():
            assert line[former] == line[english], former

    schema = _access_schema()
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(report)]
    assert errors == []
    assert _undeclared(report, schema) == []


def test_contract_requires_the_english_access_names_and_deprecates_the_former() -> None:
    schema = _access_schema()
    line = schema["properties"]["acces"]["items"]
    assert set(RENAMED_ACCESS_REPORT) - {"tool_cost_eur"} <= set(schema["required"])
    assert set(RENAMED_ACCESS_LINE) <= set(line["required"])
    for english, former in RENAMED_ACCESS_REPORT.items():
        assert not schema["properties"][english].get("deprecated"), english
        assert schema["properties"][former]["deprecated"] is True, former
        assert former not in schema["required"], former
    for english, former in RENAMED_ACCESS_LINE.items():
        assert not line["properties"][english].get("deprecated"), english
        assert line["properties"][former]["deprecated"] is True, former
        assert former not in line["required"], former


async def test_validation_emits_english_codes_and_their_former_spelling(client: AsyncClient) -> None:
    response = await client.post("/api/v1/workflows/validate", json={"yaml": WORKFLOW_WITH_BOTH_CODES})
    assert response.status_code == 200, response.text
    body = response.json()
    issues = {i["code"]: i for i in body["errors"] + body["warnings"]}
    assert issues["gate.nothing_to_check"]["deprecated_code"] == "gate.sans_matiere"  # lang: fr-ok
    assert issues["workflow.implicit_effect"]["deprecated_code"] == "workflow.effet_implicite"  # lang: fr-ok
    assert "gate.sans_matiere" not in issues and "workflow.effet_implicite" not in issues  # lang: fr-ok
    # A code that was never renamed carries no former spelling.
    assert issues["workflow.no_human_state"].get("deprecated_code") is None


async def test_rejected_publication_carries_both_codes(client: AsyncClient, project: dict[str, Any]) -> None:
    response = await client.put(
        f"/api/v1/projects/{project['id']}/workflow", json={"yaml": WORKFLOW_WITH_BOTH_CODES}
    )
    assert response.status_code == 422, response.text
    errors = response.json()["errors"]
    gate = next(e for e in errors if e["code"] == "gate.nothing_to_check")
    assert gate["deprecated_code"] == "gate.sans_matiere"  # lang: fr-ok
    problem = contracts.load_openapi()["components"]["schemas"]["Problem"]
    assert _undeclared({"errors": errors}, problem) == []
