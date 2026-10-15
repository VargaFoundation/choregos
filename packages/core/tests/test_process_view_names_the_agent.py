"""The process view says which registry agent carries each step, and what it reads and writes (S39-13a)."""

from __future__ import annotations

from pathlib import Path

from choregos_core import parse_workflow, to_process

ROOT = Path(__file__).resolve().parents[3]
DEV_COMPLEX = ROOT / "templates/github-software-delivery/workflows/dev-complex.yaml"

INLINE = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: inline, version: 1 }
initial: todo
actors:
  quant: { type: job, role: backtest, command: [python, run.py, "--fast"], image: quant-tools }
  maker: { type: agent, role: custom, playbook: maker }
  platform: { type: system }
states:
  todo: { display: To do, kind: wait }
  made: { display: Made }
  done: { display: Done, terminal: true, kind: terminal }
transitions:
  - { id: t-make, from: todo, to: made, by: maker, outputs: [deck], output_types: { deck: pptx } }
  - { id: t-run, from: made, to: done, by: quant, gates: [no_secrets] }
"""


def _steps(text: str) -> dict[str, dict]:
    workflow, _ = parse_workflow(text)
    return {s["id"]: s for s in to_process(workflow)}


def test_a_step_names_the_registry_agent_not_the_actor_key() -> None:
    steps = _steps(DEV_COMPLEX.read_text("utf-8"))
    specify = steps["t-specify"]
    assert specify["actor"] == "spec_writer" and specify["agent"] == "spec-writer"
    assert specify["role"] == "refine" and specify["inputs"] == ["human_feedback"]
    assert specify["model"] == "profile:standard"
    assert steps["t-release-notes"]["agent"] == "release-notes-writer"


def test_a_step_that_carries_a_diff_gate_writes_the_branch() -> None:
    steps = _steps(DEV_COMPLEX.read_text("utf-8"))
    assert steps["t-implement"]["writes_branch"] is True and steps["t-implement"]["outputs"] == []
    assert steps["t-review"]["writes_branch"] is False
    assert steps["t-approve-spec"]["writes_branch"] is False, "a person does not write the branch"


def test_a_human_step_names_its_group_and_its_sla_and_no_agent() -> None:
    approve = _steps(DEV_COMPLEX.read_text("utf-8"))["t-approve-spec"]
    assert (approve["group"], approve["sla_hours"], approve["agent"]) == ("product-owners", 24, None)
    assert approve["role"] is None and approve["command"] is None


def test_a_job_carries_its_command_and_typed_outputs_pass_through() -> None:
    steps = _steps(INLINE)
    assert steps["t-run"]["command"] == ["python", "run.py", "--fast"]
    assert steps["t-run"]["image"] == "quant-tools" and steps["t-run"]["writes_branch"] is True
    assert steps["t-make"]["output_types"] == {"deck": "pptx"} and steps["t-make"]["agent"] is None


def test_the_sentence_is_unchanged() -> None:
    specify = _steps(DEV_COMPLEX.read_text("utf-8"))["t-specify"]
    assert specify["who"] == "the agent `spec_writer` (role refine, model profile:standard)"
