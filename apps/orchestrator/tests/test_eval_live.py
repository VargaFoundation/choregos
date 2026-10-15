"""Live eval cells (S37-10, ADR 0055): a case is a real stage, judged by the facts its runner measured
and billed by the ledger, never by what the fixture scripted."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_contracts import Diagnostics, Evidence, StageInput, StageResult, StageStatus
from choregos_orchestrator.activities import evals
from choregos_orchestrator.activities import stage as stage_activities
from choregos_orchestrator.config import reset_settings_cache

from .conftest import Fixture

TICKETS = sorted(evals.TICKETS.glob("*.yaml"))


def _cell(fixture: str, context: str = "rtk+headroom", **extra: Any) -> dict[str, Any]:
    return {
        "project_slug": "billing-api",
        "matrix_id": "evals-nightly",
        "backend": "claude-code",
        "model": "platform/standard",
        "with_memory": False,
        "context": context,
        "fixture": fixture,
        **extra,
    }


async def _memory_off(project_id: str) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, project_id)
        assert row is not None
        model = policy_model(row).model_dump(mode="json", exclude_none=True)
        model["memory"] = {**model.get("memory", {}), "enabled": False}
        row.json_doc = model


@pytest.fixture
async def eval_project(setup: Fixture) -> Fixture:
    await _memory_off(setup.project_id)
    return setup


# ───────────────────────── the ticket is seeded once, on the toy repository ─────────────────────────


async def test_the_toy_repository_is_committed_once_and_the_ticket_filed_once(eval_project: Fixture) -> None:
    scm = eval_project.adapters.scm
    first = await evals.seed_eval_ticket(_cell("py-bug-arrondi"))
    again = await evals.seed_eval_ticket(_cell("py-bug-arrondi"))

    assert not first["skipped"], first["reason"]
    assert first["plan"]["work_item_id"] == again["plan"]["work_item_id"], "a retry files no second ticket"
    branch = first["plan"]["eval_cell"]["base_branch"]
    assert branch.startswith("choregos-evals/python-toy-")
    assert scm.branches["varga/billing-api"][branch] == "main"
    toy = evals.REPOS / "python-toy"
    assert scm.files[("varga/billing-api", branch)] == {
        str(p.relative_to(toy)): p.read_text(encoding="utf-8")
        for p in toy.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
    }
    assert len([c for c in scm.commits if c[1] == branch]) == 1, "a retry commits nothing"


async def test_each_cell_has_its_own_ticket(eval_project: Fixture) -> None:
    none = await evals.seed_eval_ticket(_cell("py-bug-arrondi", "none"))
    rtk = await evals.seed_eval_ticket(_cell("py-bug-arrondi", "rtk"))
    other_matrix = await evals.seed_eval_ticket(_cell("py-bug-arrondi", "rtk", matrix_id="evals-other"))
    ids = {none["plan"]["work_item_id"], rtk["plan"]["work_item_id"], other_matrix["plan"]["work_item_id"]}
    assert len(ids) == 3


async def test_the_plan_asks_the_cell_s_reduction_and_fails_without_headroom(eval_project: Fixture) -> None:
    spec = yaml.safe_load((evals.TICKETS / "py-bug-arrondi.yaml").read_text(encoding="utf-8"))
    plan = (await evals.seed_eval_ticket(_cell("py-bug-arrondi")))["plan"]
    assert plan["eval_cell"]["context"] == {
        "shell_filter": "rtk",
        "compression": "headroom",
        "on_unavailable": "fail",
    }
    assert (plan["backend"], plan["model_request"], plan["role"]) == (
        "claude-code",
        "platform/standard",
        spec.get("role", "implement"),
    )
    assert len(plan["eval_cell"]["facts"]) >= len(spec["assertions"])
    alone = (await evals.seed_eval_ticket(_cell("py-bug-arrondi", context="headroom")))["plan"]
    assert alone["eval_cell"]["context"] == {"compression": "headroom", "on_unavailable": "fail"}


@pytest.mark.parametrize(
    ("cell", "why"),
    [
        (_cell("py-bug-arrondi", with_memory=True), "memory"),
        (_cell("does-not-exist"), "no reference ticket"),
    ],
)
async def test_a_case_a_live_cell_cannot_measure_is_skipped(
    eval_project: Fixture, cell: dict[str, Any], why: str
) -> None:
    seeded = await evals.seed_eval_ticket(cell)
    assert seeded["skipped"] and why in seeded["reason"]
    assert not eval_project.adapters.scm.commits


async def test_a_project_with_memory_on_cannot_run_a_cell_without_it(setup: Fixture) -> None:
    seeded = await evals.seed_eval_ticket(_cell("py-bug-arrondi"))
    assert seeded["skipped"] and "memory on" in seeded["reason"]


async def test_the_kill_switch_skips_the_reduced_cells(
    eval_project: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_CONTEXT_REDUCTION", "off")
    reset_settings_cache()
    try:
        reduced = await evals.seed_eval_ticket(_cell("py-bug-arrondi", "rtk"))
        plain = await evals.seed_eval_ticket(_cell("py-bug-arrondi", "none"))
    finally:
        monkeypatch.delenv("CHOREGOS_CONTEXT_REDUCTION")
        reset_settings_cache()
    assert reduced["skipped"] and "CHOREGOS_CONTEXT_REDUCTION" in reduced["reason"]
    assert not plain["skipped"]


# ───────────────────────── the facts judge the workspace as the scripted path does ─────────────────────────


def _run_facts(facts: dict[str, str], work: Path) -> dict[str, bool]:
    return {
        name: subprocess.run(command, shell=True, cwd=work, capture_output=True, check=False).returncode == 0
        for name, command in facts.items()
        if "_command" not in name and "_requires" not in name
    }


@pytest.mark.parametrize("path", TICKETS, ids=lambda p: p.stem)
def test_the_facts_pass_on_the_scripted_change_and_fail_on_the_toy_repository(
    path: Path, tmp_path: Path
) -> None:
    """The scripted change is the one the scripted matrix judges right: the facts must agree."""
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    toy = evals.REPOS / spec["repo"]
    facts = evals._facts(spec, toy)
    work = tmp_path / "repo"
    shutil.copytree(toy, work)
    untouched = _run_facts(facts, work)
    for relative, content in (spec["scripted"]["with_memory"].get("files") or {}).items():
        (work / relative).parent.mkdir(parents=True, exist_ok=True)
        (work / relative).write_text(content, encoding="utf-8")
    changed = _run_facts(facts, work)

    assert all(changed.values()), {k: v for k, v in changed.items() if not v}
    if spec["scripted"]["with_memory"].get("files"):
        assert not all(untouched.values()), "facts the toy repository already passes measure nothing"


@pytest.mark.parametrize("kind", ["file_absent", "file_unchanged"])
def test_a_file_fact_fails_when_the_agent_writes_outside_its_scope(kind: str, tmp_path: Path) -> None:
    spec = yaml.safe_load((evals.TICKETS / "py-hors-perimetre.yaml").read_text(encoding="utf-8"))
    toy = evals.REPOS / spec["repo"]
    work = tmp_path / "repo"
    shutil.copytree(toy, work)
    index, assertion = next((i, a) for i, a in enumerate(spec["assertions"]) if a["kind"] == kind)
    (work / assertion["path"]).parent.mkdir(parents=True, exist_ok=True)
    (work / assertion["path"]).write_text("bus: prod\n", encoding="utf-8")
    measured = _run_facts(evals._facts(spec, toy), work)
    assert measured[f"eval_{index}_{kind}"] is False
    assert all(v for name, v in measured.items() if name != f"eval_{index}_{kind}")


def test_a_required_tool_has_a_fact_of_its_own() -> None:
    facts = evals._facts(
        {"assertions": [{"kind": "command", "run": "npm test --silent", "requires": "npm"}]}, Path("/nowhere")
    )
    assert facts == {"eval_0_requires": "command -v npm >/dev/null", "eval_0_command": "npm test --silent"}


# ───────────────────────── a real stage, then the verdict ─────────────────────────


def _key_id(stage_input: StageInput) -> str:
    return stage_input.gateway_key.removeprefix("sk-fake-")


async def _live_case(
    setup: Fixture,
    fixture: str,
    context: str,
    *,
    facts: bool = True,
    status: StageStatus = StageStatus.DONE,
    billed: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The workflow's sequence, without Temporal: seed, prepare, run, account, judge."""
    seen: dict[str, Any] = {}

    def agent(stage_input: StageInput) -> StageResult:
        seen["stage_input"] = stage_input
        if billed:
            setup.adapters.gateway.record_usage(
                _key_id(stage_input),
                tokens_in=30_000,
                tokens_out=2_000,
                cached=10_000,
                model="platform/standard",
            )
        measured = dict.fromkeys(stage_input.project.fact_commands, facts)
        return StageResult(
            status=status,
            summary="eval",
            evidence=Evidence(facts=measured),
            diagnostics=Diagnostics(turns=7),
        )

    setup.adapters.executor.handler = agent
    seeded = await evals.seed_eval_ticket(_cell(fixture, context))
    assert not seeded["skipped"], seeded["reason"]
    prepared = await stage_activities.prepare_stage(seeded["plan"])
    ids = {"project_id": seeded["plan"]["project_id"], "run_id": prepared["run_id"]}
    await stage_activities.start_run(
        {
            "project_id": ids["project_id"],
            "work_item_id": seeded["plan"]["work_item_id"],
            "stage_input": prepared["stage_input"],
        }
    )
    awaited = await stage_activities.await_run({**ids, "timeout_minutes": 1, "poll_seconds": 0.05})
    await stage_activities.record_run_outcome({**ids, "result": awaited["result"]})
    await stage_activities.collect_spend(ids)
    verdict = await evals.judge_eval_run({**ids, "fixture": fixture, "context": context, "failure": ""})
    return verdict, seen


async def test_the_runner_gets_the_toy_branch_the_facts_and_the_cell_s_reduction(
    eval_project: Fixture,
) -> None:
    _, seen = await _live_case(eval_project, "py-bug-arrondi", "rtk+headroom")
    stage_input: StageInput = seen["stage_input"]
    assert stage_input.repo is not None and stage_input.repo.base_branch.startswith(
        "choregos-evals/python-toy-"
    )
    assert set(stage_input.project.fact_commands) == set(
        evals._facts(yaml.safe_load((evals.TICKETS / "py-bug-arrondi.yaml").read_text()), evals.REPOS)
    )
    assert (stage_input.context.shell_filter, stage_input.context.compression) == ("rtk", "headroom")
    assert stage_input.context.on_unavailable == "fail"


async def test_a_case_whose_facts_hold_succeeds_with_the_ledger_s_tokens(eval_project: Fixture) -> None:
    verdict, _ = await _live_case(eval_project, "py-bug-arrondi", "none")
    assert verdict["ok"] and not verdict["skipped"], verdict["reason"]
    assert verdict["usage"] == {
        "tokens_in": 30_000,
        "tokens_cached": 10_000,
        "tokens_out": 2_000,
        "cost_usd": pytest.approx(verdict["usage"]["cost_usd"]),
        "turns": 7,
    }
    assert verdict["usage"]["cost_usd"] > 0


async def test_a_case_nothing_was_sent_for_has_no_measure(eval_project: Fixture) -> None:
    verdict, _ = await _live_case(eval_project, "py-bug-arrondi", "none", billed=False)
    assert verdict["ok"] and verdict["usage"] is None


async def test_a_missing_tool_skips_the_case(eval_project: Fixture) -> None:
    verdict, _ = await _live_case(eval_project, "node-slug-longueur", "none", facts=False)
    assert verdict["skipped"] and "eval_1_requires" in verdict["reason"]


async def test_the_kill_switch_still_wins_at_prepare_time(
    eval_project: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await evals.seed_eval_ticket(_cell("py-bug-arrondi", "rtk+headroom"))
    monkeypatch.setenv("CHOREGOS_CONTEXT_REDUCTION", "off")
    reset_settings_cache()
    try:
        prepared = await stage_activities.prepare_stage(seeded["plan"])
    finally:
        monkeypatch.delenv("CHOREGOS_CONTEXT_REDUCTION")
        reset_settings_cache()
    assert "context" not in prepared["stage_input"]
    assert prepared["stage_input"]["repo"]["base_branch"].startswith("choregos-evals/")


async def test_a_false_fact_fails_the_case(eval_project: Fixture) -> None:
    verdict, _ = await _live_case(eval_project, "py-bug-arrondi", "none", facts=False)
    assert not verdict["ok"] and not verdict["skipped"]
    assert "eval_0_" in verdict["reason"]


async def test_a_ticket_that_must_block_fails_when_the_run_says_done(eval_project: Fixture) -> None:
    done, _ = await _live_case(eval_project, "py-hors-perimetre", "none")
    assert not done["ok"] and "expected status blocked" in done["reason"]


async def test_a_ticket_that_must_block_succeeds_when_the_run_blocks(eval_project: Fixture) -> None:
    blocked, _ = await _live_case(eval_project, "py-hors-perimetre", "none", status=StageStatus.BLOCKED)
    assert blocked["ok"], blocked["reason"]


@pytest.mark.parametrize(
    ("event", "payload", "why"),
    [
        ("context.degraded", {"compression": "headroom", "reason": "no answer"}, "headroom was unavailable"),
        ("context.shell_filter", {"shell_filter": "rtk", "backend": "codex", "applied": False}, "no hook"),
    ],
)
async def test_a_run_without_the_cell_s_reduction_is_skipped(
    eval_project: Fixture, event: str, payload: dict[str, Any], why: str
) -> None:
    from choregos_api.db.models import RunEvent
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    _, seen = await _live_case(eval_project, "py-bug-arrondi", "rtk+headroom")
    run_id = seen["stage_input"].run_id
    async with session_scope() as session:
        session.add(RunEvent(run_id=run_id, seq=900, type=event, payload=payload, ts=utcnow()))
    verdict = await evals.judge_eval_run(
        {"run_id": run_id, "fixture": "py-bug-arrondi", "context": "rtk+headroom", "failure": ""}
    )
    assert verdict["skipped"] and why in verdict["reason"]


async def test_a_run_given_another_reduction_is_skipped(eval_project: Fixture) -> None:
    _, seen = await _live_case(eval_project, "py-bug-arrondi", "none")
    verdict = await evals.judge_eval_run(
        {"run_id": seen["stage_input"].run_id, "fixture": "py-bug-arrondi", "context": "rtk", "failure": ""}
    )
    assert verdict["skipped"] and "shell_filter=none" in verdict["reason"]


async def test_a_run_without_result_fails_with_its_cause(eval_project: Fixture) -> None:
    seeded = await evals.seed_eval_ticket(_cell("py-bug-arrondi", "none"))
    prepared = await stage_activities.prepare_stage(seeded["plan"])
    verdict = await evals.judge_eval_run(
        {"run_id": prepared["run_id"], "fixture": "py-bug-arrondi", "context": "none", "failure": "OOMKilled"}
    )
    assert not verdict["ok"] and not verdict["skipped"] and verdict["reason"] == "OOMKilled"


async def test_the_summary_is_the_scripted_cell_s_line() -> None:
    line = await evals.summarize_eval_cell(
        {
            "backend": "claude-code",
            "model": "platform/standard",
            "with_memory": False,
            "context": "rtk",
            "outcomes": [
                {
                    "fixture": "a",
                    "ok": True,
                    "skipped": False,
                    "reason": "",
                    "duration_s": 10.0,
                    "usage": {
                        "tokens_in": 100,
                        "tokens_cached": 0,
                        "tokens_out": 10,
                        "cost_usd": 0.5,
                        "turns": 3,
                    },
                },
                {
                    "fixture": "b",
                    "ok": False,
                    "skipped": False,
                    "reason": "x",
                    "duration_s": 30.0,
                    "usage": None,
                },
                {
                    "fixture": "c",
                    "ok": False,
                    "skipped": True,
                    "reason": "npm",
                    "duration_s": 0.0,
                    "usage": None,
                },
            ],
        }
    )
    assert (line["cases"], line["skipped"], line["metered"], line["success_rate"]) == (2, 1, 1, 0.5)
    assert (line["median_cost_usd"], line["median_turns"], line["median_duration_s"]) == (0.5, 3.0, 20.0)
    assert line["context"] == "rtk" and not line["validated"]
