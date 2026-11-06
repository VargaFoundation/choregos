# SPDX-License-Identifier: Apache-2.0
"""Eval activities: running a cell of the matrix and publishing the results.

A cell = (backend x model x memory) played on the reference work items of
`tests/fixtures/tickets`. A work item's verdict is **never** declarative: the toy repository is
copied into a throwaway workspace, the change is applied, then the work item's assertions are
really checked (file present, pattern present or absent, file untouched, test command green).

S37-09 (ADR 0055): a cell also has a context reduction (`none`, `rtk`, `headroom`,
`rtk+headroom`), and its tokens and cost are read from the ledger: every ticket is played under
its own gateway key, and what the gateway says that key spent is the cell's measure. In scripted
mode the fixture's `scripted_usage` is billed on a fake gateway; a real gateway is never billed
by the evals, so a ticket nothing was sent for has no measure, never a guessed one.

S37-10 (ADR 0055): a live cell plays each ticket as a real stage. `seed_eval_ticket` commits the
toy repository to a branch of the eval project's repository and files an eval work item; the
`EvalMatrix` workflow then runs it like any stage (`prepare_stage`, `start_run`, `await_run`,
`collect_spend`); `judge_eval_run` reads the verdict from the run: the ticket's assertions are
compiled into fact commands the runner measures in its workspace, and the tokens are the ledger's.

S57-09 (ADR 0122 §9): a cell also has a door for its run's tools, `mcp` (the model's MCP tools only)
or `scripts` (those and the tool bridge, from a script). A cell plays the reference tickets and, named
by `agent_cases`, the agent cases of `choregos_playbooks/evals/agent_cases`, each `repetitions` times.
In scripted mode an agent case goes through the real doors on the fakes (`eval_doors`) and is judged
by its answer and its assertions; a live cell forces `tools.scripts` on or off in the stage input
(`StagePlan.eval_cell`), and a run that did not get the cell's door is skipped. A cell's line gains
what its runs did with their tools (calls by origin, scripts and their errors, actions proposed and
executed), and `publish_matrix` compares the cells that differ only by their door
(`eval_comparison`: a paired bootstrap interval and a verdict).

S57-11a: a live cell plays the agent cases too. `seed_eval_ticket` provisions, idempotently, the case's
eval project beside the matrix's (its connectors on fake scenes, its policy, its case-local agent:
`eval_live_cases`) and files the case's task there; the run is a stage like any other, and
`judge_eval_run` scores its answer and journal with the case's assertions, once its governance parity
holds: a run whose ledger, events and bridge lines disagree is `invalid`, reported and never counted.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shlex
import shutil
import statistics
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from choregos_api.db.models import (
    Action,
    Agent,
    AgentVersion,
    Connector,
    ConnectorOperation,
    CostLedger,
    Event,
    ModelProfileRow,
    OrgConnector,
    OrgConnectorProject,
    OrgPlugin,
    OrgPluginFile,
    PolicyDef,
    Project,
    ProjectOperationPolicy,
    Run,
    RunEvent,
    WorkItem,
)
from choregos_api.services import (
    WorkItemBirth,
    active_policy,
    new_work_item,
    policy_model,
    tracker_is_internal,
)
from choregos_api.services.initiator import initiator
from choregos_contracts import EventType
from choregos_core import elapsed_seconds, utcnow
from choregos_core.agent_catalogue import PLUGIN_CASE, plugin_ref
from choregos_core.tool_scripts_switch import tool_scripts_refusal
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .. import eval_live_cases as live_cases
from ..config import get_settings
from ..eval_comparison import compare_cells
from . import eval_doors
from .base import ProjectBundle, db, project_bundle
from .repositories import repo_slug as _repo_slug

MIN_SUCCESS_RATE = 0.7
ASSERTION_TIMEOUT_S = 300.0
FIXTURES = Path(__file__).resolve().parents[5] / "tests" / "fixtures"
TICKETS = FIXTURES / "tickets"
REPOS = FIXTURES / "repos"
CONTEXTS = ("none", "rtk", "headroom", "rtk+headroom")
EVAL_KEY_BUDGET_USD = 50.0
EVAL_KEY_TTL_S = 3 * 3600
#: What a live cell's context asks of the runner (ADR 0055). `fail`, not `bypass`: a cell that
#: measures headroom and ran without it would measure nothing.
LIVE_CONTEXTS: dict[str, dict[str, str]] = {
    "none": {},
    "rtk": {"shell_filter": "rtk"},
    "headroom": {"compression": "headroom", "on_unavailable": "fail"},
    "rtk+headroom": {"shell_filter": "rtk", "compression": "headroom", "on_unavailable": "fail"},
}
#: The doors a cell may give its run's tools (ADR 0122): what `tools.scripts` a live cell forces into the
#: stage input — none in an `mcp` cell, the bridge with the policy's default bound in a `scripts` cell.
TOOLS = eval_doors.TOOLS
LIVE_TOOLS: dict[str, dict[str, int] | None] = {"mcp": None, "scripts": {"max_concurrent_calls": 4}}
#: The journal's events of the tool scripts (S57-07, S57-08).
CLIENT_EVENT, SCRIPT_EVENT, SCRIPT_CALL_EVENT = "tools.client", "tools.script", "tools.script.call"
EVAL_BRANCH_PREFIX = "choregos-evals"
_NOT_TOY = {"__pycache__", ".pytest_cache", "node_modules", ".venv"}


@dataclass(slots=True)
class Check:
    """Verdict of an assertion: `ok=None` means skipped (tool absent)."""

    label: str
    ok: bool | None
    detail: str = ""


@activity.defn(name="list_fixtures")
async def list_fixtures(payload: dict[str, Any]) -> list[str]:
    """The reference tickets shipped in `tests/fixtures/tickets` (S12-01).

    None is a broken image, not an empty matrix: the 0.18.1 orchestrator image did not carry them,
    and a live matrix played nothing, then published four cells of zero cases (S37-13).
    """
    from temporalio.exceptions import ApplicationError

    found = sorted(path.stem for path in TICKETS.glob("*.yaml")) if TICKETS.is_dir() else []
    if not found:
        raise ApplicationError(
            f"no reference ticket in {TICKETS}: the image does not carry tests/fixtures",
            type="EvalFixturesMissing",
            non_retryable=True,
        )
    return found


@activity.defn(name="run_eval_cell")
async def run_eval_cell(payload: dict[str, Any]) -> dict[str, Any]:
    """Play the reference work items for a backend x model pair, with and without memory.

    Each work item has a **deterministic** success criterion: the grade depends on no subjective
    judgement. A work item whose tools are missing (no `npm` on the runner) is skipped, never
    counted as passed: the same rule as the runner's DoD loop.
    """
    fixtures: list[str] = payload.get("fixtures", [])
    context = _context(payload)
    tools = _tools(payload)
    repetitions = _repetitions(payload)
    outcomes: list[dict[str, Any]] = []
    for name in fixtures:
        spec = _load_spec(name)
        if spec is None:
            continue
        for repetition in range(repetitions):
            outcome = await _play(spec, payload)
            outcomes.append(
                {
                    **outcome,
                    "fixture": name,
                    "repetition": repetition,
                    "agent": _ticket_agent(spec),
                    # The scripted change calls no platform tool; its model calls are the gateway's.
                    "observation": {
                        **_no_tool_use(tools),
                        "requests": (outcome.get("usage") or {}).get("requests"),
                    },
                    "door": "mcp",
                }
            )
            with contextlib.suppress(RuntimeError):
                activity.heartbeat({"fixture": name, "repetition": repetition})
    for name in payload.get("agent_cases") or []:
        for repetition in range(repetitions):
            outcomes.append({**await _play_agent_case(str(name), payload, tools), "repetition": repetition})
            with contextlib.suppress(RuntimeError):
                activity.heartbeat({"agent_case": name, "repetition": repetition})
    return _cell(payload, context, outcomes)


@activity.defn(name="summarize_eval_cell")
async def summarize_eval_cell(payload: dict[str, Any]) -> dict[str, Any]:
    """A live cell's line of the matrix, from the verdicts `judge_eval_run` gave (S37-10)."""
    return _cell(payload, _context(payload), list(payload.get("outcomes", [])))


def _tools(payload: dict[str, Any]) -> str:
    tools = str(payload.get("tools", "mcp"))
    if tools not in TOOLS:
        raise ApplicationError(
            f"unknown tools door {tools!r}, expected one of {', '.join(TOOLS)}", non_retryable=True
        )
    return tools


def _repetitions(payload: dict[str, Any]) -> int:
    repetitions = payload.get("repetitions", 1)
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or not 1 <= repetitions <= 100:
        raise ApplicationError(
            f"repetitions must be an integer from 1 to 100, not {repetitions!r}", non_retryable=True
        )
    return repetitions


def _ticket_agent(spec: dict[str, Any]) -> str:
    """Who a ticket measures: its stage role (`role:implement`); no registry agent runs a ticket."""
    return f"role:{spec.get('role', 'implement')}"


def _no_tool_use(tools: str) -> dict[str, Any]:
    """The observation of a run that called no platform tool and wrote no script."""
    return {
        "tools": tools,
        "requests": None,
        "calls": [],
        "actions": [],
        "security_events": [],
        "scripts": [],
    }


def _context(payload: dict[str, Any]) -> str:
    context = str(payload.get("context", "none"))
    if context not in CONTEXTS:
        raise ApplicationError(
            f"unknown context reduction {context!r}, expected one of {', '.join(CONTEXTS)}",
            non_retryable=True,
        )
    return context


def _cell(payload: dict[str, Any], context: str, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    successes: list[bool] = []
    costs: list[float] = []
    durations: list[float] = []
    metered: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    for outcome in outcomes:
        name = outcome["fixture"]
        unit = {
            "fixture": name,
            "repetition": int(outcome.get("repetition", 0)),
            "agent": outcome.get("agent"),
        }
        if outcome.get("invalid"):
            # Its governance parity is broken (S57-11a): reported, never counted, never paired.
            details.append({**unit, "invalid": True, "reason": outcome["reason"]})
            continue
        if outcome["skipped"]:
            details.append({**unit, "skipped": True, "reason": outcome["reason"]})
            continue
        successes.append(bool(outcome["ok"]))
        if outcome["usage"] is not None:
            metered.append(outcome["usage"])
            costs.append(outcome["usage"]["cost_usd"])
        durations.append(outcome["duration_s"])
        if outcome.get("observation") is not None:
            observed.append(outcome["observation"])
        details.append(
            {
                **unit,
                "ok": outcome["ok"],
                "reason": outcome["reason"],
                # `None`: the gateway counted nothing; its cost is unknown, never paired as free (S57-09).
                "cost_usd": round(float(outcome["usage"]["cost_usd"]), 6) if outcome["usage"] else None,
                **({"door": outcome["door"]} if outcome.get("door") else {}),
            }
        )
    rate = (sum(1 for s in successes if s) / len(successes)) if successes else 0.0
    return {
        "backend": payload["backend"],
        "model": payload["model"],
        "with_memory": payload["with_memory"],
        "context": context,
        "tools": str(payload.get("tools", "mcp")),
        "cases": len(successes),
        "skipped": sum(1 for d in details if d.get("skipped")),
        # Live runs whose ledger, events and bridge lines disagreed (S57-11a): out of every rate.
        "invalid": sum(1 for d in details if d.get("invalid")),
        # Cases the ledger has a measure for: tokens and cost are medians over these only.
        "metered": len(metered),
        "success_rate": round(rate, 4),
        "median_cost_usd": round(statistics.median(costs), 4) if costs else None,
        "median_tokens_in": _median(metered, "tokens_in"),
        "median_tokens_cached": _median(metered, "tokens_cached"),
        "median_tokens_out": _median(metered, "tokens_out"),
        "median_turns": _median([u for u in metered if u["turns"] is not None], "turns"),
        # The round trips (S57-01): over the cases whose model calls the gateway counted.
        "median_requests": _median([u for u in metered if u.get("requests") is not None], "requests"),
        "median_duration_s": round(statistics.median(durations), 2) if durations else None,
        **_tool_use(observed),
        "validated": bool(successes) and rate >= MIN_SUCCESS_RATE,
        "details": details,
    }


def _tool_use(observed: list[dict[str, Any]]) -> dict[str, Any]:
    """What a cell's runs did with their tools (the plan's §4.2), over the runs with an observation:

    - `median_platform_tool_calls`: the calls that reached the platform, per run;
    - `calls_by_origin`: the same over the cell, by the door they came through (`model`, `script`, `job`);
    - `script_adoption`: the share of runs with at least one script;
    - `scripts`, `script_errors`, `script_error_rate`: the scripts of the cell's runs, those in error, and
      their ratio (`None` without a script). A script is a digest the bridge journaled; it is in error
      when one of its calls was answered 400 or more, or its exit code is known and not 0
      (`eval_doors.scripts_of`);
    - `proposed_actions`, `executed_actions`: the cell's actions waiting for a person, and those whose
      write was played (`choregos_playbooks.evals.agents.PROPOSED_STATUSES`, `EXECUTED_STATUSES`).
    """
    from choregos_playbooks.evals.agents import EXECUTED_STATUSES, PROPOSED_STATUSES

    origins: dict[str, int] = {}
    for observation in observed:
        for call in observation.get("calls") or []:
            origin = str(call.get("origin") or "model")
            origins[origin] = origins.get(origin, 0) + 1
    scripts = [script for observation in observed for script in observation.get("scripts") or []]
    errors = sum(1 for script in scripts if script.get("errored"))
    actions = [action for observation in observed for action in observation.get("actions") or []]
    calls = [float(len(observation.get("calls") or [])) for observation in observed]
    return {
        "median_platform_tool_calls": round(statistics.median(calls), 1) if calls else None,
        "calls_by_origin": dict(sorted(origins.items())),
        "script_adoption": (
            round(sum(1 for o in observed if o.get("scripts")) / len(observed), 4) if observed else None
        ),
        "scripts": len(scripts),
        "script_errors": errors,
        "script_error_rate": round(errors / len(scripts), 4) if scripts else None,
        "proposed_actions": sum(1 for a in actions if a.get("status") in PROPOSED_STATUSES),
        "executed_actions": sum(1 for a in actions if a.get("status") in EXECUTED_STATUSES),
    }


def _median(usages: list[dict[str, Any]], key: str) -> float | None:
    values = [float(usage[key]) for usage in usages]
    return round(statistics.median(values), 1) if values else None


def _load_spec(name: str) -> dict[str, Any] | None:
    import yaml

    path = TICKETS / f"{name}.yaml"
    if not path.exists():
        return None
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    return spec if isinstance(spec, dict) else None


async def _play(spec: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Materialise the toy repository, apply the change, check the assertions."""
    repo = REPOS / str(spec.get("repo", ""))
    if not repo.is_dir():
        return _skip(f"toy repository absent: {spec.get('repo')}")
    async with db() as session:
        gateway = (await project_bundle(session, payload["project_slug"])).adapters.gateway
    context = str(payload.get("context", "none"))
    model = str(payload.get("model", "platform/standard"))
    key = await gateway.mint_key(
        {
            "purpose": "eval",
            "fixture": str(spec.get("id", "")),
            "context": context,
            "backend": payload.get("backend"),
        },
        EVAL_KEY_BUDGET_USD,
        EVAL_KEY_TTL_S,
        [model],
    )
    try:
        with tempfile.TemporaryDirectory(prefix="choregos-eval-") as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(repo, work)
            before = _snapshot(work)
            patch = await _apply_change(spec, payload, work)
            if patch.get("skipped"):
                return _skip(str(patch.get("reason", "")))
            turns = _bill_scripted(gateway, key.key_id, spec, context, model, patch, _tools(payload))
            verdict = await _judge(spec, work, before, patch)
        usage = await _ledger(gateway, key.key_id, turns)
    finally:
        await gateway.revoke(key.key_id)
    if verdict.get("skipped"):
        return verdict
    return {**verdict, "usage": usage, "duration_s": float(spec.get("expected_duration_s", 120.0))}


async def _judge(
    spec: dict[str, Any], work: Path, before: dict[str, str], patch: dict[str, Any]
) -> dict[str, Any]:
    """The ticket's assertions on the workspace, and the status the change declared."""
    checks = await _verify(spec, work, before)
    blocking = [c for c in checks if c.ok is False]
    missing_tools = [c for c in checks if c.ok is None]
    if missing_tools and not blocking:
        return _skip(missing_tools[0].detail)
    declared = str(patch.get("status", "done"))
    ok = not blocking and _status_expected(spec, declared)
    reason = (
        blocking[0].label + " — " + blocking[0].detail
        if blocking
        else ("" if ok else f"expected status {' or '.join(_expected_statuses(spec))}, got {declared}")
    )
    return {"ok": ok, "skipped": False, "reason": reason}


def scripted_usage(spec: dict[str, Any], context: str, tools: str = "mcp") -> dict[str, Any] | None:
    """A case's scripted usage for a cell: under `<context>/<tools>`, else under the door alone when the
    context is `none` (an agent case's `mcp` and `scripts`), else under the context alone when the door is
    `mcp` (a ticket written before S57-09: `none`, `rtk`…)."""
    usages = spec.get("scripted_usage") or {}
    for key, applies in ((f"{context}/{tools}", True), (tools, context == "none"), (context, tools == "mcp")):
        if applies and isinstance(usages.get(key), dict):
            return dict(usages[key])
    return None


def _bill_scripted(
    gateway: Any,
    key_id: str,
    spec: dict[str, Any],
    context: str,
    model: str,
    patch: dict[str, Any],
    tools: str = "mcp",
) -> int | None:
    """Bill the fixture's scripted usage on a fake gateway; returns the turns, which no ledger has.

    Only a scripted change is billed, and only on a gateway that can be billed by hand
    (`record_usage`, the fake's): a real gateway counts what was really sent, and nothing else. A usage
    with `requests` is billed as that many model calls, its tokens shared between them (S57-09): the
    gateway's spend then counts the round trips the case scripts.
    """
    if patch.get("source") != "scripted":
        return patch.get("turns") if isinstance(patch.get("turns"), int) else None
    usage = scripted_usage(spec, context, tools)
    record = getattr(gateway, "record_usage", None)
    if not isinstance(usage, dict) or record is None:
        return None
    requests = max(1, int(usage.get("requests", 1)))
    totals = (int(usage["tokens_in"]), int(usage["tokens_out"]), int(usage.get("tokens_cached", 0)))
    for request in range(requests):
        # The remainder goes to the first call: the sums are the usage's, to the token.
        tokens_in, tokens_out, cached = (
            t // requests + (t % requests if request == 0 else 0) for t in totals
        )
        record(key_id, tokens_in=tokens_in, tokens_out=tokens_out, cached=cached, model=model)
    return int(usage["turns"]) if "turns" in usage else None


async def _ledger(gateway: Any, key_id: str, turns: int | None) -> dict[str, Any] | None:
    """What the gateway says the ticket's key spent; `None` when nothing went through it."""
    spend = await gateway.spend(key_id)
    if not spend.requests:
        return None
    return {
        "tokens_in": spend.tokens_in,
        "tokens_cached": spend.tokens_cached,
        "tokens_out": spend.tokens_out,
        "cost_usd": spend.cost_usd,
        "turns": turns,
        "requests": spend.requests,
    }


def _skip(reason: str) -> dict[str, Any]:
    return {"ok": False, "skipped": True, "reason": reason, "usage": None, "duration_s": 0.0}


def _tool_scripts_refusal(bundle: ProjectBundle) -> str | None:
    """Why a `scripts` cell of this eval project cannot be offered the bridge (S57-05b): the deployment's
    brake, the organisation's `disabled`, or no choice and the deployment's default off. `None`: it can."""
    settings = get_settings()
    return tool_scripts_refusal(
        bundle.org_tool_scripts,
        deployment_default=settings.tool_scripts,
        deployment_allowed=settings.tool_scripts_allowed,
    )


def load_agent_case(name: str) -> dict[str, Any] | None:
    """An agent case by its name, `None` when absent: a file of `choregos_playbooks/evals/agent_cases`, or
    a plugin's matrix case `plugin:<plugin>/<case>` as the offline plugins hold it (#1372)."""
    from choregos_playbooks.evals.agents import AGENT_CASES_DIR, load_case, plugin_case

    if name.startswith(PLUGIN_CASE):
        return plugin_case(name)
    path = AGENT_CASES_DIR / f"{name}.yaml"
    return load_case(path) if path.is_file() else None


async def installed_agent_case(session: Any, org_id: str, name: str) -> dict[str, Any] | None:
    """An agent case as a live cell plays it: a plugin's matrix case as the organisation INSTALLED it (its
    file kept in `org_plugin_files`, #1372), else as `load_agent_case` finds it."""
    import yaml

    named = plugin_ref(name)
    if named is None:
        return load_agent_case(name)
    plugin = (
        await session.execute(select(OrgPlugin).where(OrgPlugin.org_id == org_id, OrgPlugin.name == named[0]))
    ).scalar_one_or_none()
    refs = (((plugin.manifest if plugin else None) or {}).get("contents") or {}).get("matrix_cases") or []
    ref = next((str(r) for r in refs if Path(str(r)).stem == named[1]), None)
    if plugin is None or ref is None:
        return load_agent_case(name)
    kept = (
        await session.execute(
            select(OrgPluginFile).where(
                OrgPluginFile.org_id == org_id,
                OrgPluginFile.digest == plugin.digest,
                OrgPluginFile.path == ref,
            )
        )
    ).scalar_one_or_none()
    if kept is None:
        return load_agent_case(name)
    case = yaml.safe_load(bytes(kept.content).decode("utf-8"))
    return {**dict(case), "name": named[1], "plugin": named[0]} if isinstance(case, dict) else None


def _plugin_connectors(case: dict[str, Any]) -> list[str]:
    """The case's connectors that are a plugin's server (`plugin:<plugin>/<server>`, #1372)."""
    return [
        str(name)
        for name, declared in (case.get("connectors") or {}).items()
        if str((declared or {}).get("kind", "")).startswith(PLUGIN_CASE)
    ]


async def _play_agent_case(name: str, payload: dict[str, Any], tools: str) -> dict[str, Any]:
    """An agent case in a scripted cell (S57-09): played through the cell's door on the fakes
    (`eval_doors.play`), its scripted usage billed on the fake gateway under its own key, then judged by
    its answer and by what its run did (`choregos_playbooks.evals.agents.judge`)."""
    from choregos_adapters.errors import AdapterError
    from choregos_playbooks.evals.agents import judge

    case = load_agent_case(name)
    if case is None:
        return {**_skip(f"no agent case {name}"), "fixture": name}
    unit = {"fixture": name, "agent": str(case["agent"])}
    if tools not in (case.get("tools_cells") or ["mcp"]):
        return {**_skip(f"the case is not played in the {tools} cell (its tools_cells)"), **unit}
    async with db() as session:
        gateway = (await project_bundle(session, payload["project_slug"])).adapters.gateway
    context, model = str(payload.get("context", "none")), str(payload.get("model", "platform/standard"))
    key = await gateway.mint_key(
        {
            "purpose": "eval",
            "agent_case": name,
            "context": context,
            "tools": tools,
            "backend": payload.get("backend"),
        },
        EVAL_KEY_BUDGET_USD,
        EVAL_KEY_TTL_S,
        [model],
    )
    try:
        played = await eval_doors.play(case, tools)
        turns = _bill_scripted(gateway, key.key_id, case, context, model, {"source": "scripted"}, tools)
        usage = await _ledger(gateway, key.key_id, turns)
    except AdapterError as unplayable:
        if not _plugin_connectors(case):
            raise  # a scene the fakes do not have is the case's error
        # A plugin's server with no fake in this process (#1372): not played, never failed.
        return {**_skip(str(unplayable)), **unit}
    finally:
        await gateway.revoke(key.key_id)
    observation = {**played.observation, "requests": usage["requests"] if usage else None}
    verdict = judge(case, played.answer, observation)
    return {
        "ok": verdict["ok"],
        "skipped": False,
        "reason": verdict["reason"],
        "usage": usage,
        "duration_s": float(case.get("expected_duration_s", 60.0)),
        **unit,
        "observation": observation,
        "door": played.door,
    }


async def _apply_change(spec: dict[str, Any], payload: dict[str, Any], work: Path) -> dict[str, Any]:
    """Apply the agent's change in the workspace.

    In scripted mode (CI, `CHOREGOS_FAKES=1`), the fixture supplies the diff: the matrix is
    reproducible and the assertions bear on real files. With a real executor, the runner's
    `StageResult` is read back.
    """
    scripted = spec.get("scripted") or {}
    variant = scripted.get("with_memory" if payload.get("with_memory") else "without_memory")
    if variant is None:
        variant = scripted.get("with_memory")
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        executor = bundle.adapters.executor
    result = getattr(executor, "result_for", lambda _run: None)(str(spec.get("id", "")))
    if result is not None:
        diagnostics = getattr(result, "diagnostics", None)
        return {
            "status": str(result.status),
            "source": "executor",
            "turns": getattr(diagnostics, "turns", None),
        }
    if variant is None:
        return {"skipped": True, "reason": f"no scripted change for {spec.get('id')}"}
    for relative, content in (variant.get("files") or {}).items():
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return {"status": str(variant.get("status", "done")), "source": "scripted"}


def _snapshot(work: Path) -> dict[str, str]:
    return {
        str(path.relative_to(work)): path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(work.rglob("*"))
        if path.is_file()
    }


async def _verify(spec: dict[str, Any], work: Path, before: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    for raw in spec.get("assertions") or []:
        kind = str(raw.get("kind"))
        if kind == "command":
            checks.append(await _check_command(raw, work))
        else:
            checks.append(_check_file(kind, raw, work, before))
    return checks


def _check_file(kind: str, raw: dict[str, Any], work: Path, before: dict[str, str]) -> Check:
    relative = str(raw.get("path", ""))
    path = work / relative
    label = f"{kind}:{relative}"
    if kind == "file_exists":
        return Check(label, path.is_file(), "expected file absent" if not path.is_file() else "")
    if kind == "file_absent":
        return Check(label, not path.exists(), "file created although it must not be")
    if kind == "file_unchanged":
        now = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
        return Check(label, now == before.get(relative), "file modified outside what was expected")
    if kind == "file_matches":
        if not path.is_file():
            return Check(label, False, "file absent")
        content = path.read_text(encoding="utf-8", errors="replace")
        found = re.search(str(raw.get("pattern", "")), content) is not None
        expect_absent = bool(raw.get("absent", False))
        detail = f"pattern {'present' if found else 'absent'}: {raw.get('pattern')}"
        return Check(label, found is not expect_absent, detail)
    return Check(label, False, f"unknown assertion: {kind}")


async def _check_command(raw: dict[str, Any], work: Path) -> Check:
    command = str(raw.get("run", ""))
    label = f"command:{command}"
    required = raw.get("requires")
    if required and shutil.which(str(required)) is None:
        return Check(label, None, f"tool absent on this runner: {required}")
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=work,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=ASSERTION_TIMEOUT_S)
    except TimeoutError:
        process.kill()
        await process.wait()
        return Check(label, False, f"timed out ({ASSERTION_TIMEOUT_S:.0f} s)")
    output = stdout.decode("utf-8", errors="replace").strip()
    return Check(label, process.returncode == 0, "" if process.returncode == 0 else output[-600:])


@activity.defn(name="seed_eval_ticket")
async def seed_eval_ticket(payload: dict[str, Any]) -> dict[str, Any]:
    """Commit the ticket's toy repository and file its eval work item; returns the stage plan.

    Replayable (ADR 0008): the branch is named by the toy tree's digest and an identical file is
    not rewritten; the work item's key is a digest of the matrix run and the cell, so a retry finds
    the one it filed. A case the eval project cannot measure faithfully is skipped, with its cause.
    """
    context = _context(payload)
    tools = _tools(payload)
    if payload.get("agent_case"):
        return await _seed_agent_case(payload, context, tools)
    fixture = str(payload["fixture"])
    spec = _load_spec(fixture)
    if spec is None:
        return _skip(f"no reference ticket {fixture}")
    if payload.get("with_memory"):
        return _skip("a live cell does not wire memory: run it with `with_memory: [false]`")
    toy = REPOS / str(spec.get("repo", ""))
    if not toy.is_dir():
        return _skip(f"toy repository missing: {spec.get('repo')}")
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        depot = bundle.config.repo
        if depot is None:
            return _skip(f"the eval project {bundle.slug} has no repository to commit the toy one to")
        if not await tracker_is_internal(session, bundle.project):
            return _skip(f"the eval project {bundle.slug} has an external tracker: eval tickets stay inside")
        if bundle.engine.memory_enabled():
            return _skip(f"the eval project {bundle.slug} has memory on: its cells would not be without it")
        if context != "none" and not get_settings().context_reduction:
            return _skip("the deployment refuses context reduction (CHOREGOS_CONTEXT_REDUCTION)")
        if tools == "scripts" and (scripts_refused := _tool_scripts_refusal(bundle)):
            return _skip(scripts_refused)

        files = _toy_files(toy)
        digest = sha256(repr(sorted(files.items())).encode()).hexdigest()[:10]
        branch = f"{EVAL_BRANCH_PREFIX}/{spec['repo']}-{digest}"
        repo = _repo_slug(depot.url)
        await bundle.adapters.scm.ensure_branch(repo, branch, depot.default_branch)
        await bundle.adapters.scm.commit_files(
            repo, branch, files, f"chore(evals): toy repository {spec['repo']}"
        )

        cell = (
            "|".join(str(payload.get(k, "")) for k in ("matrix_id", "backend", "model"))
            + f"|{context}|{fixture}"
            # A cell's door and a repetition are a ticket of their own; the `mcp` cell's first keeps the key
            # it had before S57-09, so a matrix retried across the upgrade files no second ticket.
            + (f"|{tools}" if tools != "mcp" else "")
            + (f"|{payload['repetition']}" if int(payload.get("repetition", 0)) else "")
        )
        key = f"EVAL-{sha256(cell.encode()).hexdigest()[:10]}"
        item = (
            await session.execute(
                select(WorkItem).where(WorkItem.project_id == bundle.project.id, WorkItem.tracker_key == key)
            )
        ).scalar_one_or_none()
        if item is None:
            item = await new_work_item(
                session,
                bundle.project,
                WorkItemBirth(
                    tracker_key=key,
                    title=f"[eval] {spec.get('title', fixture)}",
                    body=str(spec.get("prompt", "")),
                    created_by="eval",
                    initiated_by=initiator("eval"),
                    labels=("eval", f"eval:{fixture}"),
                    allowed_paths=tuple(spec.get("allowed_paths") or ()),
                ),
            )
        plan = {
            "project_id": bundle.project.id,
            "work_item_id": item.id,
            "transition_id": "eval",
            "role": str(spec.get("role", "implement")),
            "from_state": item.state,
            "to_state": item.state,
            "actor": "eval",
            "attempt": 1,
            "backend": payload.get("backend"),
            "model_request": str(payload.get("model", "platform/standard")),
            "eval_cell": {
                "context": LIVE_CONTEXTS[context],
                "tools": LIVE_TOOLS[tools],
                "base_branch": branch,
                "facts": _facts(spec, toy),
            },
        }
        return {"ok": False, "skipped": False, "reason": "", "plan": plan}


def _toy_files(toy: Path) -> dict[str, str]:
    return {
        str(path.relative_to(toy)): path.read_text(encoding="utf-8")
        for path in sorted(toy.rglob("*"))
        if path.is_file() and not _NOT_TOY & set(path.relative_to(toy).parts)
    }


def _facts(spec: dict[str, Any], toy: Path) -> dict[str, str]:
    """The ticket's assertions as fact commands, run by the runner in its workspace once the agent
    is done (`dod.facts`): exit 0 is true. A `requires` gets a fact of its own, so a missing tool
    skips the case instead of failing it — the same rule as `_check_command`."""
    facts: dict[str, str] = {}
    for index, raw in enumerate(spec.get("assertions") or []):
        kind = str(raw.get("kind"))
        if kind == "command":
            if raw.get("requires"):
                facts[f"eval_{index}_requires"] = f"command -v {shlex.quote(str(raw['requires']))} >/dev/null"
            facts[f"eval_{index}_command"] = str(raw.get("run", ""))
        else:
            facts[f"eval_{index}_{kind}"] = "python3 -c " + shlex.quote(_file_check(kind, raw, toy))
    return facts


def _file_check(kind: str, raw: dict[str, Any], toy: Path) -> str:
    """The same verdicts as `_check_file`, as a program the runner's image can run."""
    relative = str(raw.get("path", ""))
    head = f"import hashlib, pathlib, re, sys\np = pathlib.Path({relative!r})\n"
    if kind == "file_exists":
        return head + "sys.exit(0 if p.is_file() else 1)"
    if kind == "file_absent":
        return head + "sys.exit(1 if p.exists() else 0)"
    if kind == "file_unchanged":
        before = toy / relative
        if not before.is_file():
            return head + "sys.exit(1 if p.exists() else 0)"
        digest = sha256(before.read_bytes()).hexdigest()
        same = f"hashlib.sha256(p.read_bytes()).hexdigest() == {digest!r}"
        return head + f"sys.exit(0 if p.is_file() and {same} else 1)"
    if kind == "file_matches":
        pattern = str(raw.get("pattern", ""))
        absent = bool(raw.get("absent", False))
        return head + (
            "if not p.is_file():\n    sys.exit(1)\n"
            f"found = re.search({pattern!r}, p.read_text(encoding='utf-8', errors='replace')) is not None\n"
            f"sys.exit(0 if found is not {absent!r} else 1)"
        )
    return head + f"sys.exit('unknown assertion: ' + {kind!r})"


# ───────────────────────── a live agent case (S57-11a) ─────────────────────────

#: What the case's eval project keeps of the matrix's eval project's configuration: what a run reads.
#: Its repository, its GitOps, its schedules, triggers and start rules are left behind: a case's run
#: clones nothing, ships nothing and nothing opens a work item there but the matrix.
_CASE_PROJECT_DROPS = ("repo", "gitops", "schedules", "triggers", "start_rules")
#: The project connectors a case's run needs from the matrix's eval project: where it runs, its gateway.
_CASE_PROJECT_KINDS = ("runtime", "gateway")


async def _seed_agent_case(payload: dict[str, Any], context: str, tools: str) -> dict[str, Any]:
    """An agent case in a live cell: its eval project provisioned (idempotent, ADR 0008), its task filed
    there as an eval work item, the stage plan of its agent. A case a live cell cannot play faithfully is
    skipped with its cause, and nothing real is ever touched: a connector or an agent of the same name that
    is not the matrix's makes the case skip."""
    from choregos_adapters.errors import AdapterError
    from choregos_adapters.fakes.scenes import EVAL_FAKE_SCENES_ENV, eval_fake_scenes_allowed

    name = str(payload["agent_case"])
    case = load_agent_case(name)
    if name.startswith(PLUGIN_CASE):
        # A plugin's case, as the eval organisation installed it (#1372).
        async with db() as session:
            org_id = (await project_bundle(session, payload["project_slug"])).project.org_id
            case = await installed_agent_case(session, org_id, name)
    if case is None:
        return _skip(f"no agent case {name}")
    if tools not in (case.get("tools_cells") or ["mcp"]):
        return _skip(f"the case is not played in the {tools} cell (its tools_cells)")
    if payload.get("with_memory"):
        return _skip("a live cell does not wire memory: run it with `with_memory: [false]`")
    if refused := live_cases.live_refusal(case):
        return _skip(refused)
    if not eval_fake_scenes_allowed():
        return _skip(
            f"the deployment does not serve eval fakes ({EVAL_FAKE_SCENES_ENV}): a live agent case's "
            "connectors must be fake scenes"
        )
    if context != "none" and not get_settings().context_reduction:
        return _skip("the deployment refuses context reduction (CHOREGOS_CONTEXT_REDUCTION)")
    try:
        connectors = live_cases.case_connectors(case)
    except AdapterError as unknown:
        return _skip(str(unknown))
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        if not await tracker_is_internal(session, bundle.project):
            return _skip(f"the eval project {bundle.slug} has an external tracker: eval tickets stay inside")
        if bundle.engine.memory_enabled():
            return _skip(f"the eval project {bundle.slug} has memory on: its cells would not be without it")
        # The case's project is created in the eval project's organisation: its switch is this one.
        if tools == "scripts" and (scripts_refused := _tool_scripts_refusal(bundle)):
            return _skip(scripts_refused)
        project = await _case_project(session, bundle.project, case)
        refusal = await _case_connectors(session, project, case, connectors)
        if not refusal:
            refusal = await _case_web(session, project, case)
        if not refusal:
            refusal = await _case_agent(session, project, case, connectors)
        if refusal:
            return _skip(refusal)
        item = await _case_work_item(session, project, case, payload, context, tools)
        plan = {
            "project_id": project.id,
            "work_item_id": item.id,
            "transition_id": "eval",
            "role": live_cases.LIVE_ROLE,
            "from_state": item.state,
            "to_state": item.state,
            "actor": "eval",
            "attempt": 1,
            "backend": payload.get("backend"),
            "model_request": str(payload.get("model", "platform/standard")),
            "agent": live_cases.case_agent_slug(case),
            **({"outputs": [str(case["output"])]} if case.get("output") else {}),
            # No repository and no fact: the case is judged by its answer and its journal.
            "eval_cell": {
                "context": LIVE_CONTEXTS[context],
                "tools": LIVE_TOOLS[tools],
                "base_branch": "",
                "facts": {},
            },
        }
        return {"ok": False, "skipped": False, "reason": "", "plan": plan}


async def _case_project(session: Any, source: Project, case: dict[str, Any]) -> Project:
    """The case's eval project, in the matrix's eval project's organisation: created, or brought back to
    what it must hold — the source's configuration without what a case's run does not use, its runtime
    and gateway connectors, its policy with the case's (`eval_live_cases.case_policy`)."""
    from choregos_contracts import Policy

    slug = live_cases.case_project_slug(source.slug, str(case["name"]))
    config = {
        **{k: v for k, v in (source.config or {}).items() if k not in _CASE_PROJECT_DROPS},
        "slug": slug,
        "display_name": f"[eval] {case['name']}",
    }
    project: Project | None = (
        await session.execute(select(Project).where(Project.org_id == source.org_id, Project.slug == slug))
    ).scalar_one_or_none()
    if project is None:
        project = Project(
            org_id=source.org_id,
            slug=slug,
            name=f"[eval] {case['name']}",
            status=source.status,
            config=config,
        )
        session.add(project)
        await session.flush()
    elif project.config != config:
        project.config = config
    await _copy_project_connectors(session, source, project)
    desired = live_cases.case_policy(
        policy_model(await active_policy(session, source.id)).model_dump(mode="json", exclude_none=True), case
    )
    current = await active_policy(session, project.id)
    version = current.version if current is not None else 1
    desired["metadata"] = {**(desired.get("metadata") or {}), "version": version}
    if current is None or current.json_doc != desired:
        if current is not None:
            current.is_active = False
            version += 1
            desired["metadata"]["version"] = version
        parsed = Policy.model_validate(desired)
        session.add(
            PolicyDef(
                project_id=project.id,
                name=parsed.metadata.name,
                version=version,
                yaml=_yaml(desired),
                json_doc=desired,
                is_active=True,
            )
        )
        await session.flush()
    return project


def _yaml(document: dict[str, Any]) -> str:
    import yaml

    return str(yaml.safe_dump(document, sort_keys=False, allow_unicode=True))


async def _copy_project_connectors(session: Any, source: Project, project: Project) -> None:
    """The source's runtime and gateway connectors, as references (ADR 0034): the case's run runs where
    the matrix's tickets run, on the same gateway. A kind the source does not declare is the deployment's."""
    rows = (
        await session.execute(
            select(Connector).where(
                Connector.project_id.in_([source.id, project.id]), Connector.kind.in_(_CASE_PROJECT_KINDS)
            )
        )
    ).scalars()
    by_project: dict[tuple[str, str], Connector] = {(row.project_id, row.kind): row for row in rows}
    for kind in _CASE_PROJECT_KINDS:
        wanted, held = by_project.get((source.id, kind)), by_project.get((project.id, kind))
        if wanted is None:
            if held is not None:
                await session.delete(held)
            continue
        fields = {
            "type": wanted.type,
            "config": dict(wanted.config or {}),
            "secret_ref": wanted.secret_ref,
            "secret_refs": dict(wanted.secret_refs) if wanted.secret_refs else None,
        }
        if held is None:
            session.add(Connector(project_id=project.id, kind=kind, **fields))
        else:
            for field_name, value in fields.items():
                if getattr(held, field_name) != value:
                    setattr(held, field_name, value)
    await session.flush()


def _the_matrix_s(row: OrgConnector, wanted: live_cases.CaseConnector) -> bool:
    """Whether an organisation connector is the one the matrix declares for `wanted`: its kind and type,
    the scene and nothing else in its configuration, no secret, selected projects only, written by it."""
    return (
        row.kind == wanted.kind
        and row.type == wanted.type
        and dict(row.config or {}) == wanted.config
        and not row.secret_refs
        and row.plugin_server is None
        and row.availability == "selected_projects"
        and row.created_by == live_cases.EVAL_CREATOR
    )


async def _case_connectors(
    session: Any, project: Project, case: dict[str, Any], connectors: list[live_cases.CaseConnector]
) -> str:
    """The case's connectors on their fake scenes, turned on for its eval project alone, with the case's
    policy on their writes (`eval_live_cases`'s docstring); `""`, or why the case cannot be played."""
    from choregos_adapters import spec_of

    approval = live_cases.writes_need_approval(case)
    for wanted in connectors:
        row = (
            await session.execute(
                select(OrgConnector).where(
                    OrgConnector.org_id == project.org_id, OrgConnector.name == wanted.name
                )
            )
        ).scalar_one_or_none()
        if wanted.plugin:
            refusal = await _case_plugin_server(session, project, case, wanted, row, approval)
            if refusal:
                return refusal
            continue
        if row is None:
            row = OrgConnector(
                org_id=project.org_id,
                name=wanted.name,
                kind=wanted.kind,
                type=wanted.type,
                config=wanted.config,
                created_by=live_cases.EVAL_CREATOR,
                availability="selected_projects",
            )
            session.add(row)
            await session.flush()
        elif not _the_matrix_s(row, wanted):
            return (
                f"the organisation has a connector `{wanted.name}` that is not the matrix's {wanted.kind} on "
                f"the scene {wanted.scene}: the case is not played against it (use an eval organisation)"
            )
        spec = spec_of(wanted.kind, wanted.type)
        declared = {op.name: op for op in (spec.operations if spec is not None else ())}
        held = {
            op.name: op
            for op in (
                await session.execute(
                    select(ConnectorOperation).where(ConnectorOperation.connector_id == row.id)
                )
            ).scalars()
        }
        for operation in declared.values():
            # Allowed at the organisation, on a connector only the matrix's eval projects reach: what a
            # write does is the case's project's to decide, below.
            fields = {
                "access": operation.access,
                "policy": "allowed",
                "input_schema": operation.input_schema,
                "description": operation.description or None,
            }
            known = held.get(operation.name)
            if known is None:
                known = ConnectorOperation(
                    org_id=project.org_id, connector_id=row.id, name=operation.name, groups=[], **fields
                )
                session.add(known)
                held[operation.name] = known
            else:
                for field_name, value in fields.items():
                    if getattr(known, field_name) != value:
                        setattr(known, field_name, value)
        await session.flush()
        await _turned_on(session, project, row)
        await _case_write_policies(session, project, list(held.values()), approval)
    await session.flush()
    return ""


async def _case_plugin_server(
    session: Any,
    project: Project,
    case: dict[str, Any],
    wanted: live_cases.CaseConnector,
    row: OrgConnector | None,
    approval: bool,
) -> str:
    """A plugin's server as the case's connector (#1372): the organisation's connector of that server,
    bound to its eval instance (`fake_scene`), available to the selected projects and turned on for the
    case's eval project; its writes `allowed` at the organisation but where the server says a person
    approves (`human_approval`, never lowered), tightened by the case's project as for a scene. `""`, or why
    the case cannot be played (`eval_live_cases.plugin_binding_refusal`), or — the binding written — that
    the operations it calls were never discovered: a model would call them without their schemas."""
    from choregos_adapters.registry import FAKE_SCENE_FIELD

    refusal = live_cases.plugin_binding_refusal(wanted, row)
    if refusal or row is None:
        return refusal or "unreachable"
    if dict(row.config or {}) != {FAKE_SCENE_FIELD: wanted.scene}:
        row.config = {FAKE_SCENE_FIELD: wanted.scene}
    if row.availability != "selected_projects":
        # Nobody configured it, so nobody used it: from now on only the projects it is turned on for reach it.
        row.availability = "selected_projects"
    declared = (row.plugin_server or {}).get("operations") or {}
    operations = list(
        (
            await session.execute(select(ConnectorOperation).where(ConnectorOperation.connector_id == row.id))
        ).scalars()
    )
    for operation in operations:
        floor = declared.get(operation.name) or {}
        wanted_policy = "approval" if floor.get("human_approval") else "allowed"
        if operation.access != "read" and operation.policy != wanted_policy:
            operation.policy = wanted_policy
    await session.flush()
    await _turned_on(session, project, row)
    await _case_write_policies(session, project, operations, approval)
    undiscovered = live_cases.undiscovered(case, wanted, {op.name: op.input_schema for op in operations})
    if undiscovered:
        return (
            f"the operations {', '.join(undiscovered)} of the server `{wanted.name}` have no input schema: a "
            "model would call them blind. The connector is bound to its eval instance now: discover its "
            f"tools (`POST /api/v1/orgs/<eval-org>/connectors/{wanted.name}/discover`), then play the case "
            "again"
        )
    return ""


async def _turned_on(session: Any, project: Project, row: OrgConnector) -> None:
    """The matrix's connector turned on for the case's eval project (and for no other: it is available
    to the selected projects only)."""
    enabled = (
        await session.execute(
            select(OrgConnectorProject).where(
                OrgConnectorProject.connector_id == row.id, OrgConnectorProject.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if enabled is None:
        session.add(
            OrgConnectorProject(
                org_id=project.org_id,
                connector_id=row.id,
                project_id=project.id,
                enabled_by=live_cases.EVAL_CREATOR,
            )
        )


async def _case_web(session: Any, project: Project, case: dict[str, Any]) -> str:
    """The case's fake web (`eval_live_cases.case_web`, #1378): its `web_search` connector declared or
    brought up to date with the case's pages, turned on for the case's eval project alone; `""`, or why
    the case cannot be played. A connector of the same name that is not the matrix's is never touched."""
    from choregos_adapters.fakes.scenes import FAKE_SCENE_FIELD, WEB_SCENE

    wanted = live_cases.case_web(case)
    if wanted is None:
        return ""
    named = OrgConnector.org_id == project.org_id, OrgConnector.name == wanted.name
    row = (await session.execute(select(OrgConnector).where(*named))).scalar_one_or_none()
    if row is None:
        row = OrgConnector(
            org_id=project.org_id,
            name=wanted.name,
            kind=wanted.kind,
            type=wanted.type,
            config=wanted.config,
            created_by=live_cases.EVAL_CREATOR,
            availability="selected_projects",
        )
        session.add(row)
        await session.flush()
    elif not (
        row.kind == wanted.kind
        and row.type == wanted.type
        and (row.config or {}).get(FAKE_SCENE_FIELD) == WEB_SCENE
        and not row.secret_refs
        and row.plugin_server is None
        and row.availability == "selected_projects"
        and row.created_by == live_cases.EVAL_CREATOR
    ):
        return (
            f"the organisation has a connector `{wanted.name}` that is not the matrix's fake web: the case "
            "is not played against it (use an eval organisation)"
        )
    elif row.config != wanted.config:
        # The case's pages changed: its fake web is the case's, as the connector's scene is.
        row.config = wanted.config
    await _turned_on(session, project, row)
    await session.flush()
    return ""


async def _case_write_policies(
    session: Any, project: Project, operations: list[ConnectorOperation], approval: bool
) -> None:
    """`writes: approval`: every write tightened to `approval` in the case's project; `allowed`: none."""
    for operation in operations:
        tightened = (
            await session.execute(
                select(ProjectOperationPolicy).where(
                    ProjectOperationPolicy.project_id == project.id,
                    ProjectOperationPolicy.operation_id == operation.id,
                )
            )
        ).scalar_one_or_none()
        wanted = "approval" if approval and operation.access != "read" else None
        if wanted is None and tightened is not None:
            await session.delete(tightened)
        elif wanted is not None and tightened is None:
            session.add(
                ProjectOperationPolicy(
                    org_id=project.org_id, project_id=project.id, operation_id=operation.id, policy=wanted
                )
            )
        elif wanted is not None and tightened is not None and tightened.policy != wanted:
            tightened.policy = wanted


async def _case_agent(
    session: Any, project: Project, case: dict[str, Any], connectors: list[live_cases.CaseConnector]
) -> str:
    """The case's agent: a catalogue one must be installed in the organisation (it is resolved as for any
    run); a case-local one is registered as a project agent of the case's eval project, a new version only
    when its spec changed. `""`, or why the case cannot be played."""
    slug = live_cases.case_agent_slug(case)
    spec = live_cases.case_agent_spec(case, connectors)
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == project.org_id, Agent.slug == slug))
    ).scalar_one_or_none()
    if spec is None:
        if agent is None:
            return (
                f"the catalogue agent `{slug}` is not installed in the eval organisation: install it from "
                "the agent catalogue"
            )
        return ""
    if agent is None:
        agent = Agent(
            org_id=project.org_id,
            slug=slug,
            kind="internal",
            scope="project",
            project_id=project.id,
            display_name=f"[eval] {slug}",
            description=f"The agent of the eval case {case['name']} (S57-11a).",
            status="active",
            lifecycle="active",
        )
        session.add(agent)
        await session.flush()
    elif agent.scope != "project" or agent.project_id != project.id:
        return (
            f"the organisation has an agent `{slug}` that is not the case's: the case's own agent is not "
            "registered over it (use an eval organisation)"
        )
    checksum = live_cases.spec_checksum(spec)
    latest = (
        await session.execute(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None or latest.checksum != checksum:
        version = latest.version + 1 if latest is not None else 1
        session.add(
            AgentVersion(
                agent_id=agent.id,
                org_id=project.org_id,
                version=version,
                spec=spec,
                checksum=checksum,
                created_by=live_cases.EVAL_CREATOR,
                message=f"the agent of the eval case {case['name']}",
            )
        )
        agent.stable_version = version
        await session.flush()
    return ""


async def _case_work_item(
    session: Any, project: Project, case: dict[str, Any], payload: dict[str, Any], context: str, tools: str
) -> WorkItem:
    """The case's eval work item: its task, keyed by the matrix run, the cell and the repetition, so that
    a retry finds the one it filed."""
    name = str(case["name"])
    cell = (
        "|".join(str(payload.get(k, "")) for k in ("matrix_id", "backend", "model"))
        + f"|{context}|agent:{name}|{tools}"
        + (f"|{payload['repetition']}" if int(payload.get("repetition", 0)) else "")
    )
    key = f"EVAL-{sha256(cell.encode()).hexdigest()[:10]}"
    item: WorkItem | None = (
        await session.execute(
            select(WorkItem).where(WorkItem.project_id == project.id, WorkItem.tracker_key == key)
        )
    ).scalar_one_or_none()
    if item is None:
        item = await new_work_item(
            session,
            project,
            WorkItemBirth(
                tracker_key=key,
                title=f"[eval] {name}",
                body=str(case.get("task", "")),
                created_by="eval",
                initiated_by=initiator("eval"),
                labels=("eval", f"eval:{name}"),
            ),
        )
    return item


@activity.defn(name="judge_eval_run")
async def judge_eval_run(payload: dict[str, Any]) -> dict[str, Any]:
    """A live case's verdict, from its run: the status it ended with, the facts the runner measured,
    the tokens the ledger counted. A run that did not get the cell's reduction is skipped: counting
    it would credit the reduction with a run made without it.

    An agent case (`agent_case`, S57-11a) is judged by its answer and its journal against the case's
    assertions (`choregos_playbooks.evals.agents.judge`), once its governance parity holds
    (`eval_live_cases.parity`): a run whose ledger, events and bridge lines disagree is `invalid`."""
    agent_case = str(payload.get("agent_case") or "")
    fixture = agent_case or str(payload["fixture"])
    spec = {} if agent_case else (_load_spec(fixture) or {})
    async with db() as session:
        run = await session.get(Run, payload["run_id"])
        if run is None:
            return {**_skip(f"no run {payload['run_id']}"), "fixture": fixture}
        events = (
            (await session.execute(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)))
            .scalars()
            .all()
        )
        tools = _tools(payload)
        case: dict[str, Any] = {}
        if agent_case:
            owner = await session.get(Project, run.project_id)
            case = await installed_agent_case(session, owner.org_id if owner else "", agent_case) or {}
        unit = {
            "fixture": fixture,
            "agent": str(case.get("agent", "")) if agent_case else _ticket_agent(spec),
            "repetition": int(payload.get("repetition", 0)),
        }
        unfaithful = _unfaithful(run, list(events), LIVE_CONTEXTS[_context(payload)]) or _unfaithful_door(
            run, list(events), tools
        )
        if unfaithful:
            return {**_skip(unfaithful), **unit}
        usage = _usage_of(run)
        duration = elapsed_seconds(run.started_at, run.ended_at) if run.ended_at else 0.0
        observation = await _observed(session, run, list(events), tools)
        if not agent_case:
            verdict = _verdict(spec, run.result, str(payload.get("failure") or ""))
            return {
                **verdict,
                "usage": usage,
                "duration_s": float(duration),
                **unit,
                "observation": observation,
            }
        measured = {"usage": usage, "duration_s": float(duration), **unit, "observation": observation}
        broken = await _parity_of(session, run, list(events))
        if broken:
            return {"ok": False, "skipped": False, "invalid": True, "reason": broken, **measured}
        project = await session.get(Project, run.project_id)
        verdict = _agent_case_verdict(
            case, run.result, observation, project.slug if project else "", str(payload.get("failure") or "")
        )
    return {**verdict, **measured, "door": "bridge" if observation["scripts"] else "mcp"}


def _usage_of(run: Run) -> dict[str, Any] | None:
    """What the gateway counted for the run, once its spend is collected; `None` when it counted nothing."""
    tokens = dict(run.tokens or {}) if run.spend_collected else {}
    turns = ((run.result or {}).get("diagnostics") or {}).get("turns")
    requests = tokens.get("requests")
    if int(tokens.get("tokens_in", 0)) + int(tokens.get("tokens_out", 0)) <= 0:
        return None
    return {
        "tokens_in": int(tokens.get("tokens_in", 0)),
        "tokens_cached": int(tokens.get("tokens_cached", 0)),
        "tokens_out": int(tokens.get("tokens_out", 0)),
        "cost_usd": float(tokens.get("cost_usd", 0.0)),
        "turns": turns if isinstance(turns, int) else None,
        "requests": requests if isinstance(requests, int) else None,
    }


def _agent_case_verdict(
    case: dict[str, Any],
    result: dict[str, Any] | None,
    observation: dict[str, Any],
    project_slug: str,
    failure: str,
) -> dict[str, Any]:
    """The case's judge on what the run answered and did; a run that posted nothing failed."""
    from choregos_playbooks.evals.agents import judge

    if not case:
        return {"ok": False, "skipped": False, "reason": "the agent case is gone from the image"}
    if not result:
        return {"ok": False, "skipped": False, "reason": failure or "the run posted no result"}
    verdict = judge(case, live_cases.live_answer(case, result, project_slug), observation)
    return {"ok": verdict["ok"], "skipped": False, "reason": verdict["reason"]}


async def _parity_of(session: Any, run: Run, events: list[RunEvent]) -> str:
    """`eval_live_cases.parity` on the run's rows: its ledger's tool lines, its `choregos.tool.called`
    events, its bridge's `tools.script.call` lines."""
    booked = (
        await session.execute(
            select(CostLedger.provider, CostLedger.model, CostLedger.origin).where(
                CostLedger.run_id == run.id, CostLedger.kind == "tool"
            )
        )
    ).all()
    called = (
        (
            await session.execute(
                select(Event.payload).where(Event.subject == run.id, Event.type == str(EventType.TOOL_CALLED))
            )
        )
        .scalars()
        .all()
    )
    return live_cases.parity(
        [{"provider": provider, "model": model, "origin": origin} for provider, model, origin in booked],
        [dict(payload or {}) for payload in called],
        [dict(event.payload or {}) for event in events if event.type == SCRIPT_CALL_EVENT],
    )


def _unfaithful_door(run: Run, events: list[RunEvent], tools: str) -> str:
    """Why a run did not get the cell's door, or `""` when it did (S57-09). A `scripts` run must have been
    offered the bridge and have had its client written (`tools.client`, or a script journaled); an `mcp`
    run must have been offered neither. A `scripts` run whose model wrote no script got the door: not
    using it is what `script_adoption` measures."""
    offered = ((run.stage_input or {}).get("tools") or {}).get("scripts")
    clients = [event for event in events if event.type == CLIENT_EVENT]
    scripted = any(event.type in {SCRIPT_EVENT, SCRIPT_CALL_EVENT} for event in events)
    if tools == "scripts":
        if offered is None:
            return "the run was not offered tool scripts, the cell asked for them"
        if not scripted and not any((event.payload or {}).get("written") is True for event in clients):
            refused = next((str((e.payload or {}).get("error", "")) for e in clients if e.payload), "")
            why = refused or "no tools.client event"
            return f"the run was offered tool scripts but its agent got no client ({why})"
        return ""
    if offered is not None:
        return "the run was offered tool scripts, the cell is mcp"
    if clients or scripted:
        return "the run journaled tool scripts in an mcp cell"
    return ""


async def _observed(session: Any, run: Run, events: list[RunEvent], tools: str) -> dict[str, Any]:
    """What a live run did with its tools, for the cell's line: its calls from `choregos.tool.called`
    (origin `model` when the event has none, before S57-05), its security events, its actions, its scripts
    from the journal (no exit code: a live run's is the agent's shell's, which the platform does not see)."""
    rows = (
        (
            await session.execute(
                select(Event)
                .where(
                    Event.subject == run.id,
                    Event.type.in_([str(EventType.TOOL_CALLED), str(EventType.SECURITY_INJECTION_SUSPECTED)]),
                )
                .order_by(Event.ts)
            )
        )
        .scalars()
        .all()
    )
    actions = (await session.execute(select(Action).where(Action.run_id == run.id))).scalars().all()
    journal = [
        {"type": event.type, "payload": event.payload or {}}
        for event in events
        if event.type in {SCRIPT_EVENT, SCRIPT_CALL_EVENT}
    ]
    requests = (run.tokens or {}).get("requests")
    return {
        "tools": tools,
        "requests": requests if isinstance(requests, int) else None,
        "calls": [
            {
                "tool": (row.payload or {}).get("tool"),
                "origin": (row.payload or {}).get("origin") or "model",
                "status_code": (row.payload or {}).get("status_code"),
            }
            for row in rows
            if row.type == str(EventType.TOOL_CALLED)
        ],
        "actions": [
            {"tool": action.kind.replace(".", "__", 1), "status": action.status} for action in actions
        ],
        "security_events": [
            {"type": row.type, "tool": (row.payload or {}).get("tool")}
            for row in rows
            if row.type == str(EventType.SECURITY_INJECTION_SUSPECTED)
        ],
        "scripts": eval_doors.scripts_of({"journal": journal}),
    }


def _unfaithful(run: Run, events: list[RunEvent], asked: dict[str, str]) -> str:
    """Why a run did not run with the cell's reduction, or `""` when it did."""
    given = (run.stage_input or {}).get("context") or {}
    for field in ("shell_filter", "compression"):
        ran, wanted = given.get(field, "none"), asked.get(field, "none")
        if ran != wanted:
            return f"the run was given {field}={ran}, the cell asked {wanted}"
    for event in events:
        if event.type == "context.shell_filter" and (event.payload or {}).get("applied") is False:
            return f"{(event.payload or {}).get('backend')} has no hook for the shell filter: rtk did not run"
        if event.type == "context.degraded":
            return f"headroom was unavailable: {(event.payload or {}).get('reason', '')}"
    return ""


def _expected_statuses(spec: dict[str, Any]) -> list[str]:
    """`expect_status`: one status or a list of them, `done` when absent."""
    expected = spec.get("expect_status", "done")
    return [str(s) for s in expected] if isinstance(expected, list) else [str(expected)]


def _status_expected(spec: dict[str, Any], status: str) -> bool:
    """A ticket that expects the agent to stop (`blocked`, `needs_human`) wants one of those
    statuses; one that expects `done` only refuses `blocked`, as before S37-17. An out-of-scope
    ticket may accept both ways of not doing the work: declaring itself blocked, or asking (#736)."""
    stops = [s for s in _expected_statuses(spec) if s != "done"]
    return status in stops if stops else status != "blocked"


def _verdict(spec: dict[str, Any], result: dict[str, Any] | None, failure: str) -> dict[str, Any]:
    if not result:
        return {"ok": False, "skipped": False, "reason": failure or "the run posted no result"}
    facts = ((result.get("evidence") or {}).get("facts")) or {}
    asked = [
        name for name in _facts(spec, REPOS / str(spec.get("repo", ""))) if not name.endswith("_requires")
    ]
    missing_tool = [name for name in facts if name.endswith("_requires") and facts[name] is not True]
    if missing_tool:
        return _skip(f"a tool the ticket needs is missing on the runner ({missing_tool[0]})")
    false = [name for name in asked if facts.get(name) is not True]
    status = str(result.get("status", ""))
    if false:
        return {"ok": False, "skipped": False, "reason": f"{false[0]} is not true"}
    if not _status_expected(spec, status):
        expected = " or ".join(_expected_statuses(spec))
        return {"ok": False, "skipped": False, "reason": f"expected status {expected}, got {status}"}
    return {"ok": True, "skipped": False, "reason": ""}


@activity.defn(name="publish_matrix")
async def publish_matrix(payload: dict[str, Any]) -> dict[str, Any]:
    """Publish the matrix: `model_profiles.validated_backends` and `GET /models/matrix`, and, in each
    model's `params.matrix_comparisons`, its cells that differ only by their door compared
    (`eval_comparison.compare_cells`, S57-09). The comparisons stay in the matrix's own payload: the API's
    `ModelMatrixEntry` is unchanged."""
    results: list[dict[str, Any]] = payload.get("results", [])
    comparisons = compare_cells(results)
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        by_model: dict[str, list[dict[str, Any]]] = {}
        for entry in results:
            by_model.setdefault(entry["model"], []).append(entry)
        for model, entries in by_model.items():
            row = (
                await session.execute(
                    select(ModelProfileRow).where(
                        ModelProfileRow.litellm_model == model, ModelProfileRow.scope == "platform"
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = ModelProfileRow(scope="platform", name=model.split("/")[-1], litellm_model=model)
                session.add(row)
            # A run gets no context reduction unless its policy says so: a backend is validated by
            # its cells without one, never by a cell that only passed with rtk or headroom (S37-09); nor
            # by one that only passed with tool scripts, which a run is not offered either (S57-09).
            row.validated_backends = sorted(
                {
                    e["backend"]
                    for e in entries
                    if e.get("validated")
                    and e.get("context", "none") == "none"
                    and e.get("tools", "mcp") == "mcp"
                }
            )
            row.params = {
                **(row.params or {}),
                "matrix": entries,
                "matrix_comparisons": [c for c in comparisons if c["model"] == model],
                "matrix_generated_at": utcnow().isoformat(),
            }
        return {"models": len(by_model), "project": bundle.slug, "comparisons": comparisons}
