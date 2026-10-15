"""`output_schema` and `evidence_facts(measured_only)` (S38-05): a shape checked, a number measured."""

from __future__ import annotations

import json

from choregos_contracts import Evidence, StageOutputs, StageResult, StageStatus
from choregos_core import GateContext, evaluate, parse_workflow
from choregos_core.dsl.process import RESUMES

STRATEGIES = {
    "type": "object",
    "required": ["strategies"],
    "properties": {
        "strategies": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["id", "rule"],
                "properties": {"id": {"type": "string"}, "rule": {"type": "string"}},
                "additionalProperties": False,
            },
        }
    },
}


def produced(**outputs: object) -> GateContext:
    return GateContext(
        result=StageResult(status=StageStatus.DONE, summary="ok", outputs=StageOutputs(**outputs))  # type: ignore[arg-type]
    )


def schema_gate(ctx: GateContext) -> bool:
    return evaluate("output_schema", ctx, {"schemas": {"strategies": STRATEGIES}}).passed


def test_output_matching_its_schema_passes_as_object_or_json_string() -> None:
    doc = {"strategies": [{"id": "s1", "rule": "sma_cross(20, 100)"}]}
    assert schema_gate(produced(strategies=doc))
    assert schema_gate(produced(strategies=json.dumps(doc)))


def test_output_breaking_its_schema_fails_and_names_where() -> None:
    extra = {"strategies": [{"id": "s1", "rule": "x", "ticker": "AAPL"}]}
    out = evaluate("output_schema", produced(strategies=extra), {"schemas": {"strategies": STRATEGIES}})
    assert not out.passed
    assert "strategies['strategies'][0]" in out.detail
    too_many = {"strategies": [{"id": str(i), "rule": "x"} for i in range(4)]}
    assert not schema_gate(produced(strategies=too_many))
    assert not schema_gate(produced(strategies="not json"))


def test_missing_output_or_no_schema_fails() -> None:
    out = evaluate("output_schema", produced(other=1), {"schemas": {"strategies": STRATEGIES}})
    assert not out.passed and "strategies: missing" in out.detail
    assert not evaluate("output_schema", produced(strategies={}), {}).passed
    assert not evaluate("output_schema", GateContext(), {"schemas": {"strategies": STRATEGIES}}).passed


def measured(facts: dict[str, object], measured: list[str]) -> GateContext:
    evidence = Evidence(facts=facts, measured=measured)  # type: ignore[arg-type]
    return GateContext(result=StageResult(status=StageStatus.DONE, summary="ok", evidence=evidence))


def test_measured_only_refuses_a_declared_fact() -> None:
    params = {"keys": ["relevant_count"], "min": {"relevant_count": 1}, "measured_only": True}
    assert evaluate(
        "evidence_facts", measured({"relevant_count": 2}, ["facts.relevant_count"]), params
    ).passed
    out = evaluate("evidence_facts", measured({"relevant_count": 2}, []), params)
    assert not out.passed and "declared, not measured: relevant_count" in out.detail
    # Without the option, a declared fact still counts: the option changes nothing for old workflows.
    params.pop("measured_only")
    assert evaluate("evidence_facts", measured({"relevant_count": 2}, []), params).passed


WF = """
apiVersion: choregos/v1
kind: Workflow
metadata: {{ name: t, version: 1 }}
actors:
  quant: {{ type: agent, role: implement }}
  ci: {{ type: system }}
  owner: {{ type: human, group: po }}
states:
  inbox: {{ display: Inbox }}
  doing: {{ display: Doing }}
  done: {{ display: Done, terminal: true }}
transitions:
  - id: t1
    from: inbox
    to: doing
    by: quant
    outputs: {outputs}
    gates: [{{ name: output_schema, params: {{ schemas: {{ strategies: {schema} }} }} }}]
  - {{ id: t2, from: doing, to: done, by: ci }}
defaults:
  from_any_agent_state: {{ on_question: doing }}
"""


def errors(outputs: str, schema: str) -> list[str]:
    _wf, report = parse_workflow(WF.format(outputs=outputs, schema=schema), strict=False)
    return [i.message for i in report.errors if i.code == "gate.output_schema"]


def test_validator_refuses_a_schema_that_is_not_one_or_an_undeclared_output() -> None:
    assert errors("[strategies]", "{ type: object }") == []
    assert any("not a JSON Schema" in m for m in errors("[strategies]", "{ type: aray }"))
    assert any("does not declare" in m for m in errors("[metrics]", "{ type: object }"))
    _wf, report = parse_workflow(
        WF.format(outputs="[strategies]", schema="{}").replace(
            "params: { schemas: { strategies: {} } }", "params: {}"
        ),
        strict=False,
    )
    assert any(i.code == "gate.nothing_to_check" for i in report.errors)


def test_process_view_says_what_it_checks() -> None:
    assert "JSON Schema" in RESUMES["output_schema"]
