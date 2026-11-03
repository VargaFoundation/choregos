"""A small model may route a work item to its workflow (ADR 0116).

The project's strategy says when the model is asked: never (`labels`, the behaviour before), when no
rule matched (`rules_then_llm`), or instead of the rules (`llm`). Whatever it answers, the work item
is born: in the workflow it chose when that one is active, in the default otherwise.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.fakes.model_server import FakeModelServer
from httpx import AsyncClient


def _workflow(name: str, state: str, description: str) -> str:
    return f"""apiVersion: choregos/v1
kind: Workflow
metadata: {{name: {name}, version: 1, description: "{description}"}}
actors:
  hr: {{type: human, group: hr}}
states:
  {state}: {{display: Request, kind: wait}}
  done: {{display: Done, terminal: true}}
transitions:
  - {{id: t-done, from: {state}, to: done, by: hr}}
"""


async def _two_workflows(client: AsyncClient, pid: str) -> None:
    for name, state, description in (
        ("onboarding", "arrival", "Welcome a new hire before their first day"),
        ("offboarding", "departure", "Close the accounts of an employee who leaves"),
    ):
        published = await client.put(
            f"/api/v1/projects/{pid}/workflows/{name}", json={"yaml": _workflow(name, state, description)}
        )
        assert published.status_code == 200, published.text


async def _routing(client: AsyncClient, pid: str, strategy: str, **extra: Any) -> Any:
    body = {
        "default": "onboarding",
        "rules": [{"when": {"labels_any": ["leaver"]}, "workflow": "offboarding"}],
        "strategy": strategy,
        **extra,
    }
    return await client.put(f"/api/v1/projects/{pid}/workflow-routing", json=body)


async def _born(client: AsyncClient, pid: str, title: str, **fields: Any) -> Any:
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": title, "start": False, **fields}
    )
    assert created.status_code == 201, created.text
    return created.json()


async def _route_lines() -> list[Any]:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(CostLedger).where(CostLedger.kind == "route"))).scalars())


async def _documents(item_id: str) -> dict[str, Any]:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, item_id)
        assert item is not None
        return dict(item.documents or {})


def _scripted(monkeypatch: pytest.MonkeyPatch, server: FakeModelServer) -> None:
    """The real gateway client, against a scripted model server."""
    from choregos_api.services import llm_routing

    def router(model: str, settings: Any = None) -> llm_routing.WorkflowRouter:
        return llm_routing.GatewayWorkflowRouter(
            "http://gateway", "key", "platform/cheap", transport=server.transport()
        )

    monkeypatch.setattr(llm_routing, "get_workflow_router", router)


async def test_labels_is_the_default_strategy_and_never_asks_the_model(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    assert (
        await client.put(f"/api/v1/projects/{pid}/workflow-routing", json={"default": "onboarding"})
    ).status_code == 200
    read = (await client.get(f"/api/v1/projects/{pid}/workflow-routing")).json()
    assert (read["strategy"], read["hints"]) == ("labels", {})
    item = await _born(client, pid, "An employee leaves on Friday, close the accounts")
    assert item["workflow_name"] == "onboarding", "no rule matched: the default, as before"
    assert "routing" not in await _documents(item["id"])
    assert await _route_lines() == []


async def test_rules_then_llm_asks_the_model_only_when_no_rule_matched(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    put = await _routing(
        client, pid, "rules_then_llm", hints={"offboarding": "a leaver, an employee departure"}
    )
    assert put.status_code == 200, put.text
    assert put.json()["hints"] == {"offboarding": "a leaver, an employee departure"}

    by_rule = await _born(client, pid, "Anything", labels=["leaver"])
    assert by_rule["workflow_name"] == "offboarding"
    assert await _route_lines() == [], "a rule matched: the model was not asked"

    by_model = await _born(client, pid, "Bob's departure: the employee leaves on Friday")
    assert (by_model["workflow_name"], by_model["state"]) == ("offboarding", "departure")
    routing = (await _documents(by_model["id"]))["routing"]
    assert (routing["by"], routing["model"]) == ("llm", "fake/router")
    assert "departure" in routing["reason"]
    (line,) = await _route_lines()
    assert (line.work_item_id, line.project_id, line.agent_slug) == (by_model["id"], pid, "workflow-router")
    assert line.tokens_in > 0


async def test_llm_reads_no_rule(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    assert (await _routing(client, pid, "llm")).status_code == 200
    # The rule would send it to offboarding; the model reads "new hire" and chooses onboarding.
    item = await _born(client, pid, "A new hire arrives, prepare the first day", labels=["leaver"])
    assert item["workflow_name"] == "onboarding"
    assert (await _documents(item["id"]))["routing"]["by"] == "llm"


async def test_a_model_that_hesitates_leaves_the_item_to_the_default(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "llm", default="offboarding")
    item = await _born(client, pid, "Printer is broken")
    assert item["workflow_name"] == "offboarding", "the default"
    routing = (await _documents(item["id"]))["routing"]
    assert (routing["by"], routing["reason"]) == ("default", "no workflow clearly fits")
    assert len(await _route_lines()) == 1, "the model was asked: its spend is booked"


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        (
            lambda s: s.say('{"workflow": "payroll", "reason": "pay"}'),
            "`payroll`, which is not an active workflow",
        ),
        (lambda s: s.say("not json"), "did not answer one JSON object"),
        (lambda s: s.refuse(500, {"error": {"message": "down"}}), "the gateway answered 500"),
    ],
)
async def test_a_wrong_or_failed_answer_leaves_the_item_to_the_default(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch, script: Any, reason: str
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "llm")
    server = FakeModelServer()
    script(server)
    _scripted(monkeypatch, server)
    item = await _born(client, pid, "Bob leaves")
    assert item["workflow_name"] == "onboarding"
    assert reason in (await _documents(item["id"]))["routing"]["reason"]
    ((_, sent),) = server.requests
    assert sent["model"] == "platform/cheap" and sent["response_format"] == {"type": "json_object"}
    assert "offboarding" in sent["messages"][1]["content"], "the model reads the candidates"


async def test_the_gateway_client_reads_the_answer_and_its_usage(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "llm")
    server = FakeModelServer(usage={"prompt_tokens": 321, "completion_tokens": 12})
    server.say('{"workflow": "offboarding", "reason": "an employee leaves"}')
    _scripted(monkeypatch, server)
    item = await _born(client, pid, "Bob leaves")
    assert item["workflow_name"] == "offboarding"
    (line,) = await _route_lines()
    assert (line.tokens_in, line.tokens_out, line.model) == (321, 12, "platform/cheap")


async def test_checking_a_birth_then_creating_it_asks_the_model_once(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.services import llm_routing
    from choregos_api.services.routage import Naissance, nouveau_ticket, verifier_la_naissance

    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "llm")
    asked: list[str] = []
    fake = llm_routing.FakeWorkflowRouter()

    class Counting:
        model = fake.model

        async def choose(self, candidates: Any, item: Any) -> Any:
            asked.append(item.title)
            return await fake.choose(candidates, item)

    monkeypatch.setattr(llm_routing, "get_workflow_router", lambda model, settings=None: Counting())
    birth = Naissance(tracker_key="HR-9", title="Bob's departure on Friday")
    async with session_scope() as session:
        row = await session.get(Project, pid)
        assert row is not None
        await verifier_la_naissance(session, row, birth)
        await nouveau_ticket(session, row, birth)
    assert asked == ["Bob's departure on Friday"]
    assert len(await _route_lines()) == 1


async def test_the_routing_refuses_a_hint_for_an_unknown_workflow_and_an_unknown_strategy(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    hint = await _routing(client, pid, "llm", hints={"payroll": "salaries"})
    assert hint.status_code == 422 and "payroll" in hint.text
    assert (await _routing(client, pid, "magic")).status_code == 422
    assert (await _routing(client, pid, "llm", hints={"onboarding": "x" * 501})).status_code == 422


async def test_preview_says_where_an_item_would_be_born_and_creates_nothing(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "rules_then_llm")
    url = f"/api/v1/projects/{pid}/workflow-routing/preview"
    rule = (await client.post(url, json={"title": "x", "labels": ["leaver"]})).json()
    assert rule == {"workflow": "offboarding", "by": "rule", "reason": "routing rule 1", "model": None}
    model = (await client.post(url, json={"title": "A new hire arrives Monday"})).json()
    assert (model["workflow"], model["by"], model["model"]) == ("onboarding", "llm", "fake/router")
    items = (await client.get(f"/api/v1/projects/{pid}/work-items")).json()
    assert (items["items"] if isinstance(items, dict) else items) == []
    (line,) = await _route_lines()
    assert (line.work_item_id, line.project_id) == (None, pid), "a preview's call is real spend"


async def test_a_put_that_says_nothing_of_the_strategy_keeps_it(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A client written before ADR 0116 (a CLI, a script) sends only the default and the rules."""
    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "rules_then_llm", hints={"offboarding": "leavers"})
    kept = await client.put(
        f"/api/v1/projects/{pid}/workflow-routing", json={"default": "offboarding", "rules": []}
    )
    assert kept.status_code == 200, kept.text
    assert (kept.json()["strategy"], kept.json()["hints"]) == ("rules_then_llm", {"offboarding": "leavers"})
    read = (await client.get(f"/api/v1/projects/{pid}/workflow-routing")).json()
    assert (read["default"], read["strategy"], read["hints"]) == (
        "offboarding",
        "rules_then_llm",
        {"offboarding": "leavers"},
    )
