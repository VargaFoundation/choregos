"""A job step (ADR 0056): a command the runner executes, with no model; the DSL knows it as one."""

from __future__ import annotations

import pytest
from choregos_contracts import JobActor, StateKind
from choregos_core import ValidationError, WorkflowEngine, parse_workflow
from choregos_core.dsl.exigences import exigences
from choregos_core.dsl.graph import to_graph, to_mermaid
from choregos_core.dsl.process import to_process

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: {{ name: research, version: 1 }}
actors:
  strategist: {{ type: agent, role: strategize }}
  engine: {{ type: job, role: backtest, command: [choregos-quant, backtest, --out, out]{extra} }}
  owner: {{ type: human, group: owner }}
states:
  inbox: {{ display: Inbox }}
  strategized: {{ display: Strategized }}
  backtested: {{ display: Backtested }}
  needs_human: {{ display: Needs a human, kind: wait }}
  done: {{ display: Done, terminal: true }}
  rejected: {{ display: Rejected, terminal: true }}
transitions:
  - {{ id: t-strategize, from: inbox, to: strategized, by: strategist }}
  - id: t-backtest
    from: strategized
    to: backtested
    by: engine
    on_fail: {{ to: strategized, max_attempts: 2, escalate_to: rejected }}
  - {{ id: t-review, from: backtested, to: done, by: owner }}
  - {{ id: t-fail, from: "*agent", to: needs_human, by: owner }}
  - {{ id: t-abandon, from: needs_human, to: rejected, by: owner }}
"""


def _workflow(extra: str = ""):  # type: ignore[no-untyped-def]
    workflow, report = parse_workflow(WORKFLOW.format(extra=extra), strict=False)
    assert report.valid, [i.format() for i in report.errors]
    return workflow


def test_a_job_actor_is_parsed_with_its_argv() -> None:
    actor = _workflow(", image: quant, max_minutes: 30").actors["engine"]
    assert isinstance(actor, JobActor)
    assert actor.command == ["choregos-quant", "backtest", "--out", "out"]
    assert (actor.image, actor.max_minutes) == ("quant", 30)


@pytest.mark.parametrize(
    "extra",
    [
        ", image: ghcr.io/evil/image:latest",  # a reference, not a name the platform maps
        ", max_minutes: 0",
        ", model: profile:by_size",  # a job has no model
    ],
)
def test_a_job_refuses_what_it_cannot_honour(extra: str) -> None:
    with pytest.raises(ValidationError):
        parse_workflow(WORKFLOW.format(extra=extra), strict=False)


def test_a_job_without_a_command_is_refused() -> None:
    text = WORKFLOW.format(extra="").replace("command: [choregos-quant, backtest, --out, out]", "command: []")
    with pytest.raises(ValidationError):
        parse_workflow(text, strict=False)


def test_the_engine_runs_a_job_as_a_job_with_its_command_and_no_model() -> None:
    engine = WorkflowEngine(_workflow(", image: quant, max_minutes: 30"))
    transition = engine.select_transition("strategized")
    assert engine.actor_kind(transition) == "job"
    assert engine.agent_of(transition) is None
    assert engine.run_request(transition) == {
        "role": "backtest",
        "max_minutes": 30,
        "job": {"command": ["choregos-quant", "backtest", "--out", "out"], "image": "quant", "tools": {}},
    }
    agent = engine.run_request(engine.select_transition("inbox"))
    assert agent["role"] == "strategize" and "job" not in agent


def test_a_job_names_the_connector_operations_it_calls() -> None:
    engine = WorkflowEngine(_workflow(", tools: [{connector: marketdata, tools: [get_bars]}]"))
    request = engine.run_request(engine.select_transition("strategized"))
    assert request["job"]["tools"] == {"marketdata": ["get_bars"]}
    with pytest.raises(ValidationError):  # a connector named without operations would open nothing
        parse_workflow(WORKFLOW.format(extra=", tools: [{connector: marketdata, tools: []}]"), strict=False)


def test_the_agent_wildcard_covers_a_job_state() -> None:
    """A job fails, times out or is cancelled like an agent: `*agent` must catch it."""
    assert "strategized" in _workflow().agent_driven_states()


def test_a_backwards_job_transition_needs_a_bound_on_its_retries() -> None:
    text = WORKFLOW.format(extra="").replace(
        "    on_fail: { to: strategized, max_attempts: 2, escalate_to: rejected }\n", ""
    )
    text = text.replace("to: backtested\n    by: engine", "to: inbox\n    by: engine")
    try:
        _, report = parse_workflow(text, strict=False)
        codes = [i.code for i in report.errors]
    except ValidationError as exc:
        codes = [i.code for i in exc.issues]
    assert "transition.unbounded_retry" in codes


def test_the_views_say_it_is_a_job() -> None:
    workflow = _workflow()
    lanes = {node["id"]: node["lane"] for node in to_graph(workflow)["nodes"]}
    assert lanes["strategized"] == "job"
    assert "engine (backtest)" in to_mermaid(workflow)
    step = next(s for s in to_process(workflow) if s["id"] == "t-backtest")
    assert step["actor_type"] == "job" and "no model" in step["who"]


def test_a_job_needs_a_runtime_and_no_gateway() -> None:
    workflow = _workflow()
    workflow.actors = {k: v for k, v in workflow.actors.items() if k != "strategist"}
    needs = {e.capacite: e.raisons for e in exigences([workflow])}
    assert any("job engine" in reason for reason in needs["runtime"])
    assert "gateway" not in needs
    assert workflow.states["needs_human"].kind == StateKind.WAIT
