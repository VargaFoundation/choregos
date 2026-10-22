# SPDX-License-Identifier: Apache-2.0
"""Activités d'évals : exécution d'une cellule de la matrice et publication des résultats.

Une cellule = (backend × modèle × mémoire) jouée sur les tickets de référence de
`tests/fixtures/tickets`. Le verdict d'un ticket n'est **jamais** déclaratif : le dépôt
jouet est recopié dans un espace de travail jetable, la modification est appliquée, puis
les assertions du ticket sont vérifiées pour de bon (fichier présent, motif présent ou
absent, fichier non touché, commande de test verte).

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

from choregos_api.db.models import ModelProfileRow, Run, RunEvent, WorkItem
from choregos_api.services import Naissance, le_tracker_est_interne, nouveau_ticket
from choregos_api.services.initiator import initiator
from choregos_core import elapsed_seconds, utcnow
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..config import get_settings
from .base import db, project_bundle
from .gates import _repo_slug

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
EVAL_BRANCH_PREFIX = "choregos-evals"
_NOT_TOY = {"__pycache__", ".pytest_cache", "node_modules", ".venv"}


@dataclass(slots=True)
class Check:
    """Verdict d'une assertion : `ok=None` signifie ignorée (outil absent)."""

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
    """Joue les tickets de référence pour un couple backend × modèle, avec et sans mémoire.

    Chaque ticket a un critère de succès **déterministe** : la note ne dépend d'aucun
    jugement subjectif. Un ticket dont les outils manquent (pas de `npm` sur le runner)
    est ignoré, jamais compté comme réussi — même règle que la boucle DoD du runner.
    """
    fixtures: list[str] = payload.get("fixtures", [])
    context = _context(payload)
    outcomes: list[dict[str, Any]] = []
    for name in fixtures:
        spec = _load_spec(name)
        if spec is None:
            continue
        outcomes.append({**await _play(spec, payload), "fixture": name})
        with contextlib.suppress(RuntimeError):
            activity.heartbeat({"fixture": name})
    return _cell(payload, context, outcomes)


@activity.defn(name="summarize_eval_cell")
async def summarize_eval_cell(payload: dict[str, Any]) -> dict[str, Any]:
    """A live cell's line of the matrix, from the verdicts `judge_eval_run` gave (S37-10)."""
    return _cell(payload, _context(payload), list(payload.get("outcomes", [])))


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
    for outcome in outcomes:
        name = outcome["fixture"]
        if outcome["skipped"]:
            details.append({"fixture": name, "skipped": True, "reason": outcome["reason"]})
            continue
        successes.append(bool(outcome["ok"]))
        if outcome["usage"] is not None:
            metered.append(outcome["usage"])
            costs.append(outcome["usage"]["cost_usd"])
        durations.append(outcome["duration_s"])
        details.append({"fixture": name, "ok": outcome["ok"], "reason": outcome["reason"]})
    rate = (sum(1 for s in successes if s) / len(successes)) if successes else 0.0
    return {
        "backend": payload["backend"],
        "model": payload["model"],
        "with_memory": payload["with_memory"],
        "context": context,
        "cases": len(successes),
        "skipped": sum(1 for d in details if d.get("skipped")),
        # Cases the ledger has a measure for: tokens and cost are medians over these only.
        "metered": len(metered),
        "success_rate": round(rate, 4),
        "median_cost_usd": round(statistics.median(costs), 4) if costs else None,
        "median_tokens_in": _median(metered, "tokens_in"),
        "median_tokens_cached": _median(metered, "tokens_cached"),
        "median_tokens_out": _median(metered, "tokens_out"),
        "median_turns": _median([u for u in metered if u["turns"] is not None], "turns"),
        "median_duration_s": round(statistics.median(durations), 2) if durations else None,
        "validated": bool(successes) and rate >= MIN_SUCCESS_RATE,
        "details": details,
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
    """Matérialise le dépôt jouet, applique la modification, vérifie les assertions."""
    repo = REPOS / str(spec.get("repo", ""))
    if not repo.is_dir():
        return _skip(f"dépôt jouet absent : {spec.get('repo')}")
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
            turns = _bill_scripted(gateway, key.key_id, spec, context, model, patch)
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


def _bill_scripted(
    gateway: Any, key_id: str, spec: dict[str, Any], context: str, model: str, patch: dict[str, Any]
) -> int | None:
    """Bill the fixture's scripted usage on a fake gateway; returns the turns, which no ledger has.

    Only a scripted change is billed, and only on a gateway that can be billed by hand
    (`record_usage`, the fake's): a real gateway counts what was really sent, and nothing else.
    """
    if patch.get("source") != "scripted":
        return patch.get("turns") if isinstance(patch.get("turns"), int) else None
    usage = (spec.get("scripted_usage") or {}).get(context)
    record = getattr(gateway, "record_usage", None)
    if not isinstance(usage, dict) or record is None:
        return None
    record(
        key_id,
        tokens_in=int(usage["tokens_in"]),
        tokens_out=int(usage["tokens_out"]),
        cached=int(usage.get("tokens_cached", 0)),
        model=model,
    )
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
    }


def _skip(reason: str) -> dict[str, Any]:
    return {"ok": False, "skipped": True, "reason": reason, "usage": None, "duration_s": 0.0}


async def _apply_change(spec: dict[str, Any], payload: dict[str, Any], work: Path) -> dict[str, Any]:
    """Applique la modification de l'agent dans l'espace de travail.

    En mode scripté (CI, `CHOREGOS_FAKES=1`), la fixture fournit le diff : la matrice est
    reproductible et les assertions portent sur de vrais fichiers. Avec un exécuteur réel,
    c'est le `StageResult` du runner qui est relu.
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
        return {"skipped": True, "reason": f"aucune modification scriptée pour {spec.get('id')}"}
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
        return Check(label, path.is_file(), "fichier attendu absent" if not path.is_file() else "")
    if kind == "file_absent":
        return Check(label, not path.exists(), "fichier créé alors qu'il ne devait pas l'être")
    if kind == "file_unchanged":
        now = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
        return Check(label, now == before.get(relative), "fichier modifié hors de l'attendu")
    if kind == "file_matches":
        if not path.is_file():
            return Check(label, False, "fichier absent")
        content = path.read_text(encoding="utf-8", errors="replace")
        found = re.search(str(raw.get("pattern", "")), content) is not None
        expect_absent = bool(raw.get("absent", False))
        detail = f"motif {'présent' if found else 'absent'} : {raw.get('pattern')}"
        return Check(label, found is not expect_absent, detail)
    return Check(label, False, f"assertion inconnue : {kind}")


async def _check_command(raw: dict[str, Any], work: Path) -> Check:
    command = str(raw.get("run", ""))
    label = f"command:{command}"
    required = raw.get("requires")
    if required and shutil.which(str(required)) is None:
        return Check(label, None, f"outil absent sur ce runner : {required}")
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
        return Check(label, False, f"délai dépassé ({ASSERTION_TIMEOUT_S:.0f} s)")
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
        if not await le_tracker_est_interne(session, bundle.project):
            return _skip(f"the eval project {bundle.slug} has an external tracker: eval tickets stay inside")
        if bundle.engine.memory_enabled():
            return _skip(f"the eval project {bundle.slug} has memory on: its cells would not be without it")
        if context != "none" and not get_settings().context_reduction:
            return _skip("the deployment refuses context reduction (CHOREGOS_CONTEXT_REDUCTION)")

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
        )
        key = f"EVAL-{sha256(cell.encode()).hexdigest()[:10]}"
        item = (
            await session.execute(
                select(WorkItem).where(WorkItem.project_id == bundle.project.id, WorkItem.tracker_key == key)
            )
        ).scalar_one_or_none()
        if item is None:
            item = await nouveau_ticket(
                session,
                bundle.project,
                Naissance(
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


@activity.defn(name="judge_eval_run")
async def judge_eval_run(payload: dict[str, Any]) -> dict[str, Any]:
    """A live case's verdict, from its run: the status it ended with, the facts the runner measured,
    the tokens the ledger counted. A run that did not get the cell's reduction is skipped: counting
    it would credit the reduction with a run made without it."""
    fixture = str(payload["fixture"])
    spec = _load_spec(fixture) or {}
    async with db() as session:
        run = await session.get(Run, payload["run_id"])
        if run is None:
            return {**_skip(f"no run {payload['run_id']}"), "fixture": fixture}
        events = (
            (await session.execute(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)))
            .scalars()
            .all()
        )
        unfaithful = _unfaithful(run, list(events), LIVE_CONTEXTS[_context(payload)])
        if unfaithful:
            return {**_skip(unfaithful), "fixture": fixture}
        verdict = _verdict(spec, run.result, str(payload.get("failure") or ""))
        tokens = dict(run.tokens or {}) if run.spend_collected else {}
        turns = ((run.result or {}).get("diagnostics") or {}).get("turns")
        usage = (
            {
                "tokens_in": int(tokens.get("tokens_in", 0)),
                "tokens_cached": int(tokens.get("tokens_cached", 0)),
                "tokens_out": int(tokens.get("tokens_out", 0)),
                "cost_usd": float(tokens.get("cost_usd", 0.0)),
                "turns": turns if isinstance(turns, int) else None,
            }
            if int(tokens.get("tokens_in", 0)) + int(tokens.get("tokens_out", 0)) > 0
            else None
        )
        duration = elapsed_seconds(run.started_at, run.ended_at) if run.ended_at else 0.0
    return {**verdict, "usage": usage, "duration_s": float(duration), "fixture": fixture}


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
    """Publie la matrice : `model_profiles.validated_backends` et `GET /models/matrix`."""
    results: list[dict[str, Any]] = payload.get("results", [])
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
            # its cells without one, never by a cell that only passed with rtk or headroom (S37-09).
            row.validated_backends = sorted(
                {e["backend"] for e in entries if e.get("validated") and e.get("context", "none") == "none"}
            )
            row.params = {
                **(row.params or {}),
                "matrix": entries,
                "matrix_generated_at": utcnow().isoformat(),
            }
        return {"models": len(by_model), "project": bundle.slug}
