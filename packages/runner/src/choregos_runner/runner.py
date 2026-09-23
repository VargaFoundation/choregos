"""Le runner : l'algorithme en douze étapes de docs/plan/02 §2.2.

Il est jetable et sans mémoire : tout ce qu'il produit part dans l'API interne et
l'object store. S'il meurt, l'orchestrateur le rejoue avec le même `run_id` — et
l'idempotence de l'API fait que rien n'est compté deux fois.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from choregos_contracts import (
    Artifacts,
    Diagnostics,
    StageInput,
    StageResult,
)

from .acp import AcpClient, AgentUnreachableError
from .backends import get_backend
from .client import InternalClient
from .config import RunnerSettings, get_settings
from .dod import run_checks
from .exits import Exit
from .guardrails import GuardRails
from .result import complete_result, fallback_result, load_result, repair_prompt, write_result
from .scope import check_scope
from .workspace import Workspace, workspace_env

MAX_RESULT_REPAIRS = 2
MAX_SCOPE_REMINDERS = 1


@dataclass
class RunOutcome:
    """Ce que rend un run : un code de sortie et le résultat posté."""

    exit_code: Exit
    result: StageResult | None = None
    transcript_path: Path | None = None
    detail: str = ""


@dataclass
class EventJournal:
    """Journal ACP : envoyé par lots à l'API, écrit en JSONL pour le transcript."""

    client: InternalClient | None
    path: Path
    batch_size: int = 25
    seq: int = 0
    buffer: list[dict[str, Any]] = field(default_factory=list)

    async def record(self, type_: str, payload: dict[str, Any]) -> None:
        self.seq += 1
        event = {"seq": self.seq, "type": type_, "payload": payload}
        self.buffer.append(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        if len(self.buffer) >= self.batch_size:
            await self.flush()

    async def flush(self) -> None:
        if not self.buffer or self.client is None:
            self.buffer.clear()
            return
        # Un journal perdu ne doit jamais faire échouer un run : le résultat prime.
        with contextlib.suppress(Exception):
            await self.client.post_events(self.buffer)
        self.buffer.clear()


class Runner:
    """Exécute une étape d'agent de bout en bout."""

    def __init__(self, settings: RunnerSettings | None = None, client: InternalClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self.started = time.monotonic()

    async def run(self) -> RunOutcome:
        client = self.client or InternalClient(
            self.settings.api_url, self.settings.run_id, self.settings.run_token
        )
        owns_client = self.client is None
        try:
            # 1. input
            try:
                stage_input = await client.fetch_input()
            except Exception as exc:
                detail = str(exc)
                if "409" in detail:  # résultat déjà posté : rejeu, on sort proprement
                    return RunOutcome(Exit.OK, detail="résultat déjà posté")
                return RunOutcome(Exit.INPUT_NOT_FOUND, detail=detail)
            return await self.execute(stage_input, client)
        finally:
            if owns_client:
                await client.aclose()

    async def execute(self, stage_input: StageInput, client: InternalClient) -> RunOutcome:
        workspace = Workspace(
            self.settings.workspace, self.settings.git_user_name, self.settings.git_user_email
        )
        journal = EventJournal(
            client, workspace.path / ".choregos" / "transcript.jsonl", self.settings.event_batch_size
        )
        await journal.record(
            "run.started", {"run_id": stage_input.run_id, "role": stage_input.transition.role}
        )

        # 2. clone et branche
        try:
            await workspace.prepare(stage_input)
        except Exception as exc:
            await journal.record("run.failed", {"phase": "clone", "error": str(exc)[:500]})
            await journal.flush()
            return RunOutcome(Exit.CLONE_FAILED, detail=str(exc))
        base = await workspace.base_sha(stage_input.repo.base_branch)

        # 3. contexte
        context = None
        try:
            context = await client.fetch_context()
        except Exception:
            context = None
        workspace.write_context_files(stage_input, context)

        # 4-5. backend et lancement
        backend = get_backend(stage_input.agent.backend)
        plan = backend.launch_plan(stage_input, workspace.path)
        written = plan.materialize(workspace.path)
        workspace.exclude(written)
        guards = GuardRails(stage_input.permissions, list(stage_input.allowed_paths))
        env = {
            **plan.env,
            **backend.model_env(stage_input.model, stage_input.gateway_key),
            **workspace_env(stage_input),
        }

        async def on_update(params: dict[str, Any]) -> None:
            await journal.record("session/update", params)

        async def on_permission(params: dict[str, Any]) -> tuple[bool, str]:
            decision = guards.decide(params)
            await journal.record("session/request_permission", {**decision.to_event(), "params": params})
            return decision.allowed, decision.reason

        diagnostics = Diagnostics()
        agent_result: StageResult | None = None
        dod_iterations = 0
        repairs = 0
        scope_result = None

        try:
            async with AcpClient(
                plan.command,
                cwd=str(workspace.path),
                env=env,
                on_update=on_update,
                on_permission=on_permission,
            ) as agent:
                await agent.initialize()
                await agent.new_session(str(workspace.path), plan.mcp_servers)
                await journal.record("session/new", {"backend": backend.name})

                # 7. premier tour
                prompt = self._prompt(stage_input, workspace.path)
                budget_seconds = stage_input.budget.max_minutes * 60
                await agent.prompt(prompt, timeout=budget_seconds)

                # 8. boucle DoD
                max_iterations = max(0, stage_input.permissions.dod_iterations)
                report = await run_checks(stage_input, workspace.path)
                while not report.ok and dod_iterations < max_iterations:
                    dod_iterations += 1
                    await journal.record(
                        "dod.retry",
                        {"iteration": dod_iterations, "failures": [c.name for c in report.failures()]},
                    )
                    await agent.prompt(report.prompt_for_repair(), timeout=self._remaining(budget_seconds))
                    report = await run_checks(stage_input, workspace.path)

                # 9. périmètre
                scope_result = await check_scope(workspace, base, guards.allowed_paths)
                if scope_result.reverted:
                    await journal.record("scope.reverted", {"paths": scope_result.reverted})
                    await agent.prompt(scope_result.prompt(), timeout=self._remaining(budget_seconds))
                    scope_result = await check_scope(workspace, base, guards.allowed_paths)

                # 10. résultat
                load = load_result(workspace.result_path())
                while not load.ok and repairs < MAX_RESULT_REPAIRS:
                    repairs += 1
                    await journal.record("result.repair", {"attempt": repairs, "error": load.error})
                    await agent.prompt(
                        repair_prompt(load, workspace.result_path()), timeout=self._remaining(budget_seconds)
                    )
                    load = load_result(workspace.result_path())
                agent_result = load.result
                diagnostics = Diagnostics(
                    turns=agent.outcome.turns,
                    tool_calls=agent.outcome.tool_calls,
                    permission_denials=guards.denials,
                    duration_s=round(time.monotonic() - self.started, 2),
                    agent_exit=_agent_exit(agent.outcome.stop_reason, agent.outcome.cancelled),
                    dod_iterations=dod_iterations,
                    result_repairs=repairs,
                )
                if agent_result is None:
                    # Deux échecs qui se ressemblent et ne se soignent pas pareil : un agent
                    # qui a travaillé mais n'a pas écrit son résultat, et un agent dont le
                    # tour revient VIDE — clé refusée, quota atteint, fournisseur en panne.
                    # Le second accusé d'un oubli envoie l'enquête du mauvais côté.
                    if _agent_muet(agent.outcome) and not workspace.result_path().exists():
                        raison = "agent_silencieux"
                        detail = (
                            "l'agent n'a produit ni texte ni appel d'outil : vérifier l'accès "
                            f"au modèle (fin de tour : {agent.outcome.stop_reason})"
                        )
                    else:
                        raison, detail = "invalid_result", (load.error or "résultat illisible")
                    invalid = fallback_result(raison, detail)
                    final = complete_result(
                        invalid,
                        measured=report.evidence(),
                        artifacts=Artifacts(branch=stage_input.repo.work_branch),
                        diagnostics=diagnostics,
                    )
                    await self._publish(client, journal, workspace, stage_input, final, base)
                    return RunOutcome(Exit.INVALID_RESULT, final, detail=detail)
        except AgentUnreachableError as exc:
            await journal.record("agent.unreachable", {"error": str(exc)[:500]})
            await journal.flush()
            return RunOutcome(Exit.AGENT_UNREACHABLE, detail=str(exc))

        # 11-12. publication
        final = complete_result(
            agent_result,
            measured=report.evidence(),
            artifacts=Artifacts(branch=stage_input.repo.work_branch),
            diagnostics=diagnostics,
            scope_blocked=scope_result.remaining if scope_result else None,
        )
        final = await self._publish(client, journal, workspace, stage_input, final, base)
        return RunOutcome(Exit.OK, final, transcript_path=journal.path)

    # ───────────────────────── étapes internes ─────────────────────────

    def _prompt(self, stage_input: StageInput, workspace: Path) -> str:
        """Playbook rendu + tâche. Le playbook vient de l'orchestrateur, jamais du dépôt."""
        playbook = stage_input.playbook.prompt or ""
        if not playbook and stage_input.playbook.prompt_url:
            playbook = f"(playbook : {stage_input.playbook.ref})"
        task = (workspace / ".choregos" / "task.md").read_text(encoding="utf-8")
        return f"{playbook}\n\n---\n\n{task}"

    def _remaining(self, budget_seconds: int) -> float:
        elapsed = time.monotonic() - self.started
        return max(30.0, budget_seconds - elapsed)

    async def _publish(
        self,
        client: InternalClient,
        journal: EventJournal,
        workspace: Workspace,
        stage_input: StageInput,
        result: StageResult,
        base: str,
    ) -> StageResult:
        """Commit, push, transcript, puis dépôt du résultat (étapes 11 et 12)."""
        commit_message = _commit_message(stage_input, result)
        sha = await workspace.commit(commit_message)
        commits = await workspace.commits_since(base)
        additions, deletions = await workspace.diff_stats(base)
        result.artifacts.commits = commits or result.artifacts.commits
        result.artifacts.branch = stage_input.repo.work_branch
        result.evidence.diff_lines = additions + deletions
        result.evidence.diff_files = len(await workspace.changed_files(base))

        if sha and not self.settings.dry_run:
            push = await workspace.push(stage_input.repo.work_branch)
            await journal.record("git.push", {"ok": push.ok, "output": push.output[-500:]})

        await journal.record("run.result", {"status": str(result.status), "summary": result.summary})
        await journal.flush()
        transcript_url = f"{stage_input.context_pack_url or ''}".replace("context.json", "transcript.jsonl")
        result.artifacts.transcript_url = transcript_url or str(journal.path)
        result.artifacts.context_pack_url = stage_input.context_pack_url
        write_result(workspace.result_path(), result)

        try:
            await client.post_result(result)
        except Exception as exc:  # l'orchestrateur lira le résultat via l'exécuteur
            await journal.record("result.post_failed", {"error": str(exc)[:300]})
        return result


def _commit_message(stage_input: StageInput, result: StageResult) -> str:
    """Commit conventionnel, préfixé par le rôle : l'historique dit qui a fait quoi."""
    prefix = {
        "implement": "feat",
        "fix_ci": "fix",
        "address_review": "fix",
        "verify": "test",
        "refine": "docs",
        "plan": "docs",
        "release_notes": "docs",
    }.get(stage_input.transition.role, "chore")
    scope = stage_input.work_item.key.rsplit("#", 1)[-1]
    summary = result.summary.strip().splitlines()[0][:100] if result.summary else "étape Choregos"
    return f"{prefix}({scope}): {summary}"


def _agent_muet(outcome: Any) -> bool:
    """Ni un mot, ni un outil : le modèle n'a pas répondu.

    Un agent qui a parlé sans écrire son résultat est un agent distrait — on le lui
    rappelle. Un agent dont le tour revient VIDE n'a rien à se faire rappeler : sa clé est
    refusée, son quota est atteint, ou son fournisseur est en panne.

    L'appelant y ajoute l'absence de tout fichier de résultat : un agent qui en a écrit un,
    même illisible, a agi — le lui rappeler a du sens.
    """
    return outcome.messages == 0 and outcome.tool_calls == 0 and not outcome.errors


def _agent_exit(stop_reason: str, cancelled: bool) -> str:
    if cancelled:
        return "cancelled"
    return {
        "end_turn": "normal",
        "max_tokens": "limit",
        "max_turn_requests": "limit",
        "refusal": "normal",
        "timeout": "timeout",
        "error": "crashed",
    }.get(stop_reason, "normal")


async def run_stage(settings: RunnerSettings | None = None) -> RunOutcome:
    """Point d'entrée programmatique (utilisé par la CLI et les tests)."""
    return await Runner(settings).run()


def main_async(settings: RunnerSettings | None = None) -> RunOutcome:
    return asyncio.run(run_stage(settings))
