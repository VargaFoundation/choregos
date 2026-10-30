"""The access report and the issue code renamed to English (ADR 0046, S34-03, S45-02).

The access report emits the English names and, for one release, the former ones that the contract
marks `deprecated`; `role.playbook_not_found` carries its former spelling in `deprecated_code`. These
tests read the responses against `openapi.yaml`, so that the contract and what is served cannot
drift apart while both names live.
"""

from __future__ import annotations

from typing import Any

import choregos_contracts as contracts
from httpx import AsyncClient
from jsonschema import Draft202012Validator

#: English name -> former name, at the top of the access report.
RENAMED_ACCESS_REPORT = {"events": "evenements", "inferred": "deduites"}  # lang: fr-ok
#: Names the S34-03 window served and S45-02 removed.
REMOVED_REPORT_KEYS = ("refus", "cout_outils_eur")  # lang: fr-ok
REMOVED_LINE_KEYS = ("cible", "demandes", "refus")  # lang: fr-ok

WORKFLOW_WITH_THE_CODES_OF_S34_03 = """
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

WORKFLOW_WITH_A_MISSING_PLAYBOOK = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: t, version: 1 }
actors:
  dev: { type: agent, role: no_such_role }
  ci: { type: system }
states:
  inbox: { display: Inbox }
  doing: { display: Doing }
  done: { display: Done, terminal: true }
transitions:
  - { id: t1, from: inbox, to: doing, by: dev }
  - { id: t2, from: doing, to: done, by: ci }
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
            {"kind": "telepathy", "target": "/workspace/mind", "allowed": True, "reason": "ok"},
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
    assert report["events"] == 5
    for key in REMOVED_REPORT_KEYS:
        assert key not in report, key

    denied = report["access"][0]
    assert (denied["target"], denied["requests"], denied["refusals"]) == ("/workspace/.env", 1, 1)
    assert denied["reasons"] == ["sensitive file"]
    read = next(line for line in report["access"] if line["target"] == "/workspace/src/a.py")
    assert read["requests"] == 2
    # An access that cannot be classified is `other`, and its former line says `autre`.
    unclassified = next(line for line in report["access"] if line["target"] == "/workspace/mind")
    assert unclassified["nature"] == "other"

    assert len(report["acces"]) == len(report["access"])
    for line, former in zip(report["access"], report["acces"], strict=True):
        assert (line["target"], line["requests"], line["refusals"], line["reasons"]) == (
            former["target"],
            former["requests"],
            former["refusals"],
            former["motifs"],
        )
        assert former["nature"] == ("autre" if line["nature"] == "other" else line["nature"])  # lang: fr-ok
        for key in REMOVED_LINE_KEYS:
            assert key not in line and key not in former, key
        assert "motifs" not in line and "reasons" not in former  # lang: fr-ok

    schema = _access_schema()
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(report)]
    assert errors == []
    assert _undeclared(report, schema) == []


def test_contract_requires_the_english_access_names_and_deprecates_the_former() -> None:
    schema = _access_schema()
    line = schema["properties"]["access"]["items"]
    assert {"events", "refusals", "access"} <= set(schema["required"])
    assert {"target", "requests", "refusals"} <= set(line["required"])
    assert "other" in line["properties"]["nature"]["enum"]
    assert "autre" not in line["properties"]["nature"]["enum"]  # lang: fr-ok
    for english, former in {**RENAMED_ACCESS_REPORT, "access": "acces"}.items():  # lang: fr-ok
        assert not schema["properties"][english].get("deprecated"), english
        assert schema["properties"][former]["deprecated"] is True, former
        assert former not in schema["required"], former
    for key in REMOVED_REPORT_KEYS:
        assert key not in schema["properties"], key
    for key in REMOVED_LINE_KEYS:
        assert key not in line["properties"], key
        assert key not in schema["properties"]["acces"]["items"]["properties"], key  # lang: fr-ok


async def test_validation_emits_english_codes_and_their_former_spelling(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/workflows/validate", json={"yaml": WORKFLOW_WITH_THE_CODES_OF_S34_03}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    issues = {i["code"]: i for i in body["errors"] + body["warnings"]}
    # The S34-03 window ended: these two carry no former spelling any more.
    assert issues["gate.nothing_to_check"].get("deprecated_code") is None
    assert issues["workflow.implicit_effect"].get("deprecated_code") is None
    assert issues["workflow.no_human_state"].get("deprecated_code") is None

    response = await client.post(
        "/api/v1/workflows/validate", json={"yaml": WORKFLOW_WITH_A_MISSING_PLAYBOOK}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    issues = {i["code"]: i for i in body["errors"] + body["warnings"]}
    assert issues["role.playbook_not_found"]["deprecated_code"] == "role.playbook_introuvable"  # lang: fr-ok
    assert "role.playbook_introuvable" not in issues  # lang: fr-ok


async def test_rejected_publication_carries_the_english_code(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    response = await client.put(
        f"/api/v1/projects/{project['id']}/workflow", json={"yaml": WORKFLOW_WITH_THE_CODES_OF_S34_03}
    )
    assert response.status_code == 422, response.text
    errors = response.json()["errors"]
    gate = next(e for e in errors if e["code"] == "gate.nothing_to_check")
    assert gate.get("deprecated_code") is None
    problem = contracts.load_openapi()["components"]["schemas"]["Problem"]
    assert _undeclared({"errors": errors}, problem) == []
