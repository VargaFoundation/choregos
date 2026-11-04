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
    from choregos_api.services.routing import WorkItemBirth, new_work_item, verify_birth

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
    birth = WorkItemBirth(tracker_key="HR-9", title="Bob's departure on Friday")
    async with session_scope() as session:
        row = await session.get(Project, pid)
        assert row is not None
        await verify_birth(session, row, birth)
        await new_work_item(session, row, birth)
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
    assert rule == {
        "workflow": "offboarding",
        "by": "rule",
        "reason": "routing rule 1",
        "model": None,
        "confidence": None,
    }
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


# A decision model (ADR 0121): a catalogue entry enabled for `route` and `decide` is asked one choice
# question on `/v1/systemone`, and answers a workflow with its probability.

DECIDER = "platform/decide"


async def _decider(client: AsyncClient, *usages: str) -> None:
    """The catalogue entry of the decision model, created or changed to these usages."""
    base = "/api/v1/orgs/varga/models"
    entry = await client.post(base, json={"model_name": DECIDER, "usages": list(usages)})
    if entry.status_code == 409:
        listed = (await client.get(base)).json()
        (row,) = [r for r in listed["entries"] if r["model_name"] == DECIDER]
        entry = await client.patch(f"{base}/{row['id']}", json={"usages": list(usages)})
    assert entry.status_code in (200, 201), entry.text


async def test_a_catalogue_entry_for_route_and_decide_routes_as_a_decision_model(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "llm")
    await _decider(client, "decide")
    alone = await _born(client, pid, "Bob leaves on Friday")
    assert (await _documents(alone["id"]))["routing"]["model"] == "fake/router", (
        "`decide` without `route`: the chat model still routes"
    )

    await _decider(client, "route", "decide")
    item = await _born(client, pid, "Bob leaves on Friday")
    assert item["workflow_name"] == "offboarding"
    routing = (await _documents(item["id"]))["routing"]
    assert (routing["by"], routing["model"]) == ("llm", "fake/decide")
    assert routing["confidence"] == pytest.approx(0.6), "one shared word: 0.5 + 0.1"
    preview = (
        await client.post(
            f"/api/v1/projects/{pid}/workflow-routing/preview", json={"title": "Bob leaves on Friday"}
        )
    ).json()
    assert (preview["model"], preview["confidence"]) == ("fake/decide", pytest.approx(0.6))


async def test_below_the_minimum_confidence_the_default_takes_the_item(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    await _decider(client, "route", "decide")
    put = await _routing(client, pid, "llm", min_confidence=0.7)
    assert (put.status_code, put.json()["min_confidence"]) == (200, 0.7), put.text
    assert (await client.get(f"/api/v1/projects/{pid}/workflow-routing")).json()["min_confidence"] == 0.7

    held = await _born(client, pid, "Bob leaves on Friday")
    assert held["workflow_name"] == "onboarding", "0.6 < 0.7: the default"
    routing = (await _documents(held["id"]))["routing"]
    assert routing["by"] == "default" and "below the project's minimum of 0.70" in routing["reason"]
    assert routing["confidence"] == pytest.approx(0.6)
    assert len(await _route_lines()) == 1, "the model was asked: its spend is booked"

    sure = await _born(client, pid, "An employee leaves: close the accounts")
    assert sure["workflow_name"] == "offboarding", "four shared words: 0.9, above the minimum"

    kept = await _routing(client, pid, "llm")
    assert kept.json()["min_confidence"] == 0.7, "absent from a PUT: unchanged"
    cleared = await _routing(client, pid, "llm", min_confidence=None)
    assert cleared.json()["min_confidence"] is None
    taken = await _born(client, pid, "Bob leaves on Friday")
    assert taken["workflow_name"] == "offboarding", "no minimum: the choice is taken"


async def test_a_chat_model_is_never_held_to_the_minimum_confidence(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "llm", min_confidence=0.99)
    item = await _born(client, pid, "Bob's departure: the employee leaves on Friday")
    assert item["workflow_name"] == "offboarding", "a chat model answers no probability"
    assert (await _documents(item["id"]))["routing"]["confidence"] is None


@pytest.mark.parametrize("value", [-0.1, 1.5, "high"])
async def test_the_minimum_confidence_is_a_probability(
    client: AsyncClient, project: dict[str, Any], value: Any
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    assert (await _routing(client, pid, "llm", min_confidence=value)).status_code == 422


def _systemone(answer: dict[str, Any] | None, status: int = 200) -> tuple[Any, list[Any]]:
    import json

    import httpx

    sent: list[Any] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append((str(request.url), json.loads(request.content)))
        if answer is None:
            return httpx.Response(status, json={"error": {"message": "down"}})
        return httpx.Response(
            status,
            json={
                "model": "gpt-6-luna",
                "answers": {"workflow": answer},
                "usage": {"input_tokens": 157, "output_tokens": 0},
            },
        )

    return httpx.MockTransport(handle), sent


def _scripted_decider(monkeypatch: pytest.MonkeyPatch, transport: Any) -> None:
    from choregos_api.services import llm_routing

    def router(model: str, settings: Any = None) -> llm_routing.WorkflowRouter:
        return llm_routing.DecisionWorkflowRouter("http://gateway/v1", "key", model, transport=transport)

    monkeypatch.setattr(llm_routing, "get_decision_router", router)


async def test_the_decision_client_asks_one_choice_question_on_systemone(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "llm", hints={"offboarding": "a leaver"}, min_confidence=0.5)
    await _decider(client, "route", "decide")
    transport, sent = _systemone(
        {
            "type": "choice",
            "choice": "offboarding",
            "confidence": 0.86,
            "probabilities": {"offboarding": 0.93, "onboarding": 0.07},
        }
    )
    _scripted_decider(monkeypatch, transport)
    item = await _born(client, pid, "Bob leaves", body="Last day on Friday", labels=["hr"])
    assert item["workflow_name"] == "offboarding"
    ((url, body),) = sent
    assert url == "http://gateway/v1/systemone", "the base's `/v1` is not doubled"
    assert body["model"] == DECIDER
    assert body["state"] == {
        "title": "Bob leaves",
        "description": "Last day on Friday",
        "labels": ["hr"],
        "type": None,
    }
    question = body["questions"]["workflow"]
    assert question["type"] == "choice"
    criteria = question["criteria"]
    assert criteria["offboarding"] == "Close the accounts of an employee who leaves a leaver", (
        "description, hint"
    )
    assert criteria["onboarding"] == "Welcome a new hire before their first day"
    routing = (await _documents(item["id"]))["routing"]
    assert (routing["model"], routing["confidence"]) == (DECIDER, 0.93)
    assert "probability of 0.93" in routing["reason"], "probabilities[choice], not the margin (0.86)"
    (line,) = await _route_lines()
    assert (line.model, line.tokens_in, line.tokens_out) == (DECIDER, 157, 0)


# A real answer of gpt-6-luna behind LiteLLM 1.105.5, recorded in prod on 2026-11-04 (#1303): its
# `confidence` is the margin over the runner-up (0.93 - 0.07), not the probability of the answer.
RECORDED_ANSWER = {
    "type": "choice",
    "choice": "offboarding",
    "confidence": 0.86,
    "probabilities": {"offboarding": 0.93, "onboarding": 0.07},
}


@pytest.mark.parametrize(
    ("answer", "minimum", "workflow"),
    [
        # A sure answer whose margin is small: 0.54 against 0.46 is above 0.5, its `confidence` 0.08
        # is not. The threshold reads the probability, so the model's choice is taken.
        (
            {"type": "choice", "choice": "offboarding", "confidence": 0.08,
             "probabilities": {"offboarding": 0.54, "onboarding": 0.46}},
            0.5,
            "offboarding",
        ),
        # A `confidence` above the minimum does not lift a probability below it.
        (
            {"type": "choice", "choice": "offboarding", "confidence": 0.9,
             "probabilities": {"offboarding": 0.6, "onboarding": 0.4}},
            0.7,
            "onboarding",
        ),
        (RECORDED_ANSWER, 0.9, "offboarding"),
        (RECORDED_ANSWER, 0.95, "onboarding"),
    ],
)  # fmt: skip
async def test_the_minimum_reads_the_probability_of_the_choice_not_its_confidence(
    client: AsyncClient,
    project: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    answer: dict[str, Any],
    minimum: float,
    workflow: str,
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "llm", min_confidence=minimum)
    await _decider(client, "route", "decide")
    transport, _ = _systemone(answer)
    _scripted_decider(monkeypatch, transport)
    item = await _born(client, pid, "Bob leaves")
    assert item["workflow_name"] == workflow
    routing = (await _documents(item["id"]))["routing"]
    assert routing["confidence"] == answer["probabilities"]["offboarding"]


@pytest.mark.parametrize(
    "answer",
    [
        {"type": "choice", "choice": "offboarding", "confidence": 0.9},
        {"type": "choice", "choice": "offboarding", "probabilities": {"onboarding": 1.0}},
        {"type": "choice", "choice": "offboarding", "probabilities": {"offboarding": 1.5}},
        {"type": "choice", "choice": "offboarding", "probabilities": [0.9, 0.1]},
    ],
)
async def test_an_answer_without_the_probability_of_its_choice_leaves_the_item_to_the_default(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any]
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "llm")
    await _decider(client, "route", "decide")
    transport, _ = _systemone(answer)
    _scripted_decider(monkeypatch, transport)
    item = await _born(client, pid, "Bob leaves")
    assert item["workflow_name"] == "onboarding"
    assert "did not answer the question" in (await _documents(item["id"]))["routing"]["reason"]


@pytest.mark.parametrize(
    ("answer", "status", "reason"),
    [
        (
            {"type": "choice", "choice": "payroll", "confidence": 1.0, "probabilities": {"payroll": 1.0}},
            200,
            "`payroll`, which is not an active",
        ),
        ({"type": "noul", "noul": 0.9}, 200, "did not answer the question"),
        (None, 500, "the gateway answered 500"),
    ],
)
async def test_a_wrong_or_failed_decision_leaves_the_item_to_the_default(
    client: AsyncClient,
    project: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    answer: Any,
    status: int,
    reason: str,
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    await _routing(client, pid, "llm")
    await _decider(client, "route", "decide")
    transport, _ = _systemone(answer, status)
    _scripted_decider(monkeypatch, transport)
    item = await _born(client, pid, "Bob leaves")
    assert item["workflow_name"] == "onboarding"
    assert reason in (await _documents(item["id"]))["routing"]["reason"]
