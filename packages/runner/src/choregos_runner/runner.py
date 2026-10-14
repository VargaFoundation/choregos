# SPDX-License-Identifier: Apache-2.0
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
from typing import Any, cast

from choregos_contracts import (
    Artifacts,
    Diagnostics,
    StageInput,
    StageResult,
    StageStatus,
)

from .acp import AcpClient, AgentUnreachableError
from .acp.client import PromptOutcome
from .acp.guard import TurnGuard
from .backends import get_backend
from .budget import BUDGET_REASON, gateway_budget_refusal
from .client import InternalApiError, InternalClient
from .config import RunnerSettings, get_settings
from .dod import run_checks
from .exits import Exit
from .guardrails import GuardRails
from .inputs import INPUTS_DIR, InputInvalidError, inputs_markdown, mount_inputs
from .knowledge import KnowledgeInvalidError, lay_out
from .outils_locaux import arreter as arreter_outils
from .outils_locaux import demarrer_si_absent
from .outputs import OUTPUTS_DIR, expected_files, outputs_markdown, store_outputs
from .result import complete_result, fallback_result, load_result, repair_prompt, write_result
from .scope import check_scope
from .skills import SkillInvalide, poser
from .workspace import Workspace, agent_home, inline_outputs, workspace_env

MAX_RESULT_REPAIRS = 2
MAX_SCOPE_REMINDERS = 1

#: La clé, dans `artifacts.reports`, du commit où le run commence (S22-11).
RAPPORT_DEPART = "start_commit"


@dataclass
class RunOutcome:
    """Ce que rend un run : un code de sortie et le résultat posté."""

    exit_code: Exit
    result: StageResult | None = None
    transcript_path: Path | None = None
    detail: str = ""


#: Les mises à jour ACP que la console montre en direct (S25-01) : elles partent tout de suite,
#: avec ce qui attendait, au lieu d'attendre qu'un lot de 25 événements se remplisse. Un plan révisé
#: resterait sinon dans le tampon tant que l'agent ne parle pas — le plan affiché serait l'ancien.
SANS_ATTENDRE = frozenset({"plan"})


def _sans_attendre(type_: str, payload: dict[str, Any]) -> bool:
    if type_ != "session/update":
        return False
    update = payload.get("update", payload)
    return isinstance(update, dict) and update.get("sessionUpdate") in SANS_ATTENDRE


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
        if len(self.buffer) >= self.batch_size or _sans_attendre(type_, payload):
            await self.flush()

    async def flush(self) -> None:
        if not self.buffer or self.client is None:
            self.buffer.clear()
            return
        # Un journal perdu ne doit jamais faire échouer un run : le résultat prime.
        with contextlib.suppress(Exception):
            await self.client.post_events(self.buffer)
        self.buffer.clear()


#: L'objet « arbre vide » de git : la référence quand il n'y a pas d'historique d'avant.
#: `git diff $ARBRE_VIDE..HEAD` rend donc tout le contenu comme ajouté.
ARBRE_VIDE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class Runner:
    """Exécute une étape d'agent de bout en bout."""

    def __init__(self, settings: RunnerSettings | None = None, client: InternalClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self.started = time.monotonic()
        #: L'index des skills qu'un backend ne lit pas lui-même, ajouté au prompt.
        self.index_des_skills = ""
        #: Where the knowledge bases lie in the workspace (ADR 0051), added to the prompt.
        self.knowledge_index = ""
        #: Le jeton git du run (S22-07) : en mémoire seulement, pour le clone et le push.
        self.jeton_git: str | None = None

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
                    return RunOutcome(Exit.OK, detail="result already posted")
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
            client,
            workspace.path / ".choregos" / "transcript.jsonl",
            self.settings.event_batch_size,
            seq=_events_already_journaled(workspace.path / ".choregos" / "transcript.jsonl"),
        )
        await journal.record(
            "run.started", {"run_id": stage_input.run_id, "role": stage_input.transition.role}
        )

        # Les outils de la plateforme sont servis en localhost. Avec Tekton c'est un
        # sidecar ; avec `k8s_job` le pod n'a QU'UN conteneur, et personne n'écoutait —
        # l'agent se retrouvait sans aucun outil tout en croyant en avoir, son StageInput
        # lui promettant l'adresse. On la sert donc nous-mêmes quand elle est muette.
        annonce = stage_input.tools.mcp.get("choregos")
        outils = None
        try:
            outils = await demarrer_si_absent(
                (annonce.url if annonce else "") or self.settings.tools_url, client
            )
            await journal.record("tools.local", {"demarre": outils is not None})
        except Exception as exc:  # perdre ses outils ne doit pas empêcher l'étape
            await journal.record("tools.local", {"demarre": False, "error": str(exc)[:300]})

        # 2. clone et branche — avec le jeton git du run (S22-07) : sans lui, un dépôt privé rendait
        # « Repository not found ». Il reste en mémoire, jamais dans le workspace ni dans le journal.
        await self._demander_le_jeton_git(client, journal, stage_input)
        try:
            await workspace.prepare(stage_input, token=self.jeton_git)
        except Exception as exc:
            await journal.record("run.failed", {"phase": "clone", "error": str(exc)[:500]})
            await journal.flush()
            return RunOutcome(Exit.CLONE_FAILED, detail=str(exc))
        # Sans dépôt, la référence est l'arbre vide de git : tout ce que l'agent écrit est
        # donc « ajouté », ce qui est exactement la vérité pour un répertoire qui n'avait rien.
        base = await workspace.base_sha(stage_input.repo.base_branch) if stage_input.repo else ARBRE_VIDE
        # Le périmètre se juge sur ce que CE run écrit : la branche du ticket porte déjà le travail
        # des étapes d'avant, que l'agent n'a pas fait et ne peut pas défaire. Mesuré depuis la
        # base, il était imputé au chercheur d'une étude (locataire dev, 08/10), deux fois en échec
        # sur trois fichiers qu'avait poussés l'étape de cadrage.
        depart = await workspace.head_sha() if stage_input.repo else ARBRE_VIDE

        # 3. contexte
        context = await self._context(client)
        if (refus := await self._write_context(client, journal, stage_input, workspace, context)) is not None:
            return refus

        # 4-5. backend et lancement
        backend = get_backend(stage_input.agent.backend)
        plan = backend.launch_plan(stage_input, workspace.path)
        written = plan.materialize(workspace.path)
        workspace.exclude(written)
        plan.materialize_home(agent_home(stage_input))
        # Skills, then knowledge bases: the first refusal stops the run before the agent starts.
        if (
            refus := await self._poser_les_skills(client, journal, stage_input, backend, workspace)
            or await self._lay_out_knowledge(client, journal, stage_input, workspace)
        ) is not None:
            return refus
        guards = GuardRails(
            stage_input.permissions, list(stage_input.allowed_paths), workspace=str(workspace.path)
        )
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
                # Enforced, not only prompted (S27-07): past max_turns tool calls, or one call
                # repeated `loop_threshold` times in a row, the session is cancelled.
                guard=TurnGuard(
                    max_turns=stage_input.budget.max_turns, loop_threshold=self.settings.loop_threshold
                ),
            ) as agent:
                await agent.initialize()
                await _open_session(agent, journal, workspace.path, backend.name, plan.mcp_servers)

                # 7. premier tour
                prompt = self._prompt(stage_input, workspace.path)
                budget_seconds = stage_input.budget.max_minutes * 60
                # A key the gateway refuses for budget refuses every further turn too: no DoD
                # retry, no reminder, no repair — each would only be refused again (S27-01).
                refused = await _turn(agent, prompt, budget_seconds)

                # 8. boucle DoD
                max_iterations = max(0, stage_input.permissions.dod_iterations)
                report = await run_checks(stage_input, workspace.path)
                while (
                    not report.ok
                    and dod_iterations < max_iterations
                    and not refused
                    and agent.outcome.guard is None
                ):
                    dod_iterations += 1
                    await journal.record(
                        "dod.retry",
                        {"iteration": dod_iterations, "failures": [c.name for c in report.failures()]},
                    )
                    refused = await _turn(agent, report.prompt_for_repair(), self._remaining(budget_seconds))
                    report = await run_checks(stage_input, workspace.path)

                # 9. périmètre
                scope_result = await check_scope(workspace, depart, guards.allowed_paths)
                if scope_result.reverted and not refused and agent.outcome.guard is None:
                    await journal.record("scope.reverted", {"paths": scope_result.reverted})
                    refused = await _turn(agent, scope_result.prompt(), self._remaining(budget_seconds))
                    scope_result = await check_scope(workspace, depart, guards.allowed_paths)

                # 10. result — or the runner's own, when it stopped the agent (S27-07)
                if agent.outcome.guard is not None:
                    return await self._stopped_by_the_guard(
                        client, journal, workspace, stage_input, agent, report, (base, depart)
                    )
                load = load_result(workspace.result_path(), inline_outputs(stage_input), _files(stage_input))
                while not load.ok and repairs < MAX_RESULT_REPAIRS and not refused:
                    repairs += 1
                    await journal.record("result.repair", {"attempt": repairs, "error": load.error})
                    refused = await _turn(
                        agent, repair_prompt(load, workspace.result_path()), self._remaining(budget_seconds)
                    )
                    load = load_result(
                        workspace.result_path(), inline_outputs(stage_input), _files(stage_input)
                    )
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
                stop = _stop(agent, refused, agent_result, load.error, workspace.result_path())
                if stop is not None:
                    code, raison, detail = stop
                    final = complete_result(
                        fallback_result(raison, detail),
                        measured=report.evidence(),
                        artifacts=Artifacts(
                            branch=stage_input.repo.work_branch if stage_input.repo else None
                        ),
                        diagnostics=diagnostics,
                    )
                    await self._publish(client, journal, workspace, stage_input, final, base, depart)
                    return RunOutcome(code, final, detail=detail)
        except AgentUnreachableError as exc:
            await journal.record("agent.unreachable", {"error": str(exc)[:500]})
            await journal.flush()
            return RunOutcome(Exit.AGENT_UNREACHABLE, detail=str(exc))

        # 11-12. publication
        final = complete_result(
            cast(StageResult, agent_result),  # `_stop` ended the run when there was none
            measured=report.evidence(),
            artifacts=Artifacts(branch=stage_input.repo.work_branch if stage_input.repo else None),
            diagnostics=diagnostics,
            scope_blocked=scope_result.remaining if scope_result else None,
        )
        final = await self._publish(client, journal, workspace, stage_input, final, base, depart)
        await arreter_outils(outils)
        return RunOutcome(Exit.OK, final, transcript_path=journal.path)

    # ───────────────────────── étapes internes ─────────────────────────

    async def _poser_les_skills(
        self, client: Any, journal: Any, stage_input: StageInput, backend: Any, workspace: Any
    ) -> RunOutcome | None:
        """Les skills de l'agent du registre (ADR 0033) : vérifiées par leur empreinte, puis posées
        là où le backend les lit — hors du diff. Une skill manquante ou altérée arrête le run."""
        if not stage_input.skills:
            return None
        try:
            ecrites, self.index_des_skills = poser(
                await client.fetch_skills(), stage_input.skills, workspace.path, backend.skills_dir
            )
        except SkillInvalide as exc:
            await journal.record("run.failed", {"phase": "skills", "error": str(exc)[:500]})
            await journal.flush()
            return RunOutcome(Exit.SKILLS_INVALID, detail=str(exc))
        workspace.exclude(ecrites)
        return None

    async def _write_context(
        self, client: Any, journal: Any, stage_input: StageInput, workspace: Workspace, context: Any
    ) -> RunOutcome | None:
        """`task.md` and `context.md`, then the declared inputs as files in `.choregos/inputs/`
        (S31-03, ADR 0049), listed in `task.md`. An input that does not match its digest stops the
        run; an API that cannot list them (older than this runner) leaves the agent its prompt."""
        workspace.write_context_files(stage_input, context)
        # Deliverables (S31-05) are artifacts, never commits; the task says which files to write.
        workspace.exclude([f"{OUTPUTS_DIR}/"])
        task = workspace.path / ".choregos" / "task.md"
        task.write_text(task.read_text(encoding="utf-8") + outputs_markdown(stage_input), encoding="utf-8")
        if not stage_input.transition.inputs:
            return None
        workspace.exclude([f"{INPUTS_DIR}/"])
        try:
            mounted = await mount_inputs(client, workspace.path)
        except InputInvalidError as exc:
            await journal.record("run.failed", {"phase": "inputs", "error": str(exc)[:500]})
            await journal.flush()
            return RunOutcome(Exit.INPUTS_INVALID, detail=str(exc))
        except Exception as exc:
            await journal.record("inputs.mounted", {"ok": False, "error": str(exc)[:300]})
            return None
        await journal.record("inputs.mounted", {"ok": True, "inputs": [m["path"] for m in mounted]})
        if mounted:
            task.write_text(task.read_text(encoding="utf-8") + inputs_markdown(mounted), encoding="utf-8")
        return None

    async def _deliver(
        self, client: Any, journal: Any, workspace: Workspace, stage_input: StageInput, result: StageResult
    ) -> None:
        """Stores the deliverables of `.choregos/outputs/` (S31-05, ADR 0049) and lists them in
        `artifacts.files` — what the runner stored, never what the agent claims. A declared file that
        was not stored fails a `done` step: the platform does not take the deck on the agent's word."""
        stored, problems = await store_outputs(client, workspace.path, stage_input)
        result.artifacts.files = stored
        if stored or problems:
            await journal.record("outputs.stored", {"stored": sorted(stored), "refused": problems[:20]})
        missing = [name for name in expected_files(stage_input) if name not in stored]
        if missing and result.status is StageStatus.DONE:
            result.status = StageStatus.FAILED
            result.reason = "missing_outputs"
            result.summary = f"{result.summary} — declared files not delivered: {', '.join(missing)}"[:4000]

    async def _lay_out_knowledge(
        self, client: Any, journal: Any, stage_input: StageInput, workspace: Any
    ) -> RunOutcome | None:
        """The `files` knowledge bases (ADR 0051): checked against their digest, laid read-only
        under `.choregos/knowledge/`, out of the diff. A base missing or changed stops the run."""
        if not any(ref.mode == "files" for ref in stage_input.knowledge):
            return None
        try:
            written, self.knowledge_index = lay_out(
                await client.fetch_knowledge(), stage_input.knowledge, workspace.path
            )
        except (KnowledgeInvalidError, InternalApiError) as exc:
            await journal.record("run.failed", {"phase": "knowledge", "error": str(exc)[:500]})
            await journal.flush()
            return RunOutcome(Exit.KNOWLEDGE_INVALID, detail=str(exc))
        workspace.exclude(written)
        return None

    async def _stopped_by_the_guard(
        self,
        client: Any,
        journal: Any,
        workspace: Any,
        stage_input: StageInput,
        agent: Any,
        report: Any,
        shas: tuple[str, str],
    ) -> RunOutcome:
        """The result of an agent the runner stopped (`max_turns`, `loop`): failed with that reason,
        whatever the agent wrote. Its work is published like any failed stage's."""
        outcome = agent.outcome
        await journal.record("run.stopped", {"reason": outcome.guard, "detail": outcome.guard_detail[:500]})
        final = complete_result(
            fallback_result(str(outcome.guard), f"stopped by the runner: {outcome.guard_detail}"),
            measured=report.evidence(),
            artifacts=Artifacts(branch=stage_input.repo.work_branch if stage_input.repo else None),
            diagnostics=Diagnostics(
                turns=outcome.turns,
                tool_calls=outcome.tool_calls,
                duration_s=round(time.monotonic() - self.started, 2),
                agent_exit="limit",
            ),
        )
        base, depart = shas
        final = await self._publish(client, journal, workspace, stage_input, final, base, depart)
        return RunOutcome(Exit.OK, final, transcript_path=journal.path)

    @staticmethod
    async def _context(client: Any) -> Any:
        """The context pack, or nothing: memory is optional (ADR 0007)."""
        try:
            return await client.fetch_context()
        except Exception:
            return None

    def _prompt(self, stage_input: StageInput, workspace: Path) -> str:
        """Playbook rendu + tâche. Le playbook vient de l'orchestrateur, jamais du dépôt."""
        playbook = stage_input.playbook.prompt or ""
        if not playbook and stage_input.playbook.prompt_url:
            playbook = f"(playbook: {stage_input.playbook.ref})"
        task = (workspace / ".choregos" / "task.md").read_text(encoding="utf-8")
        if self.index_des_skills:
            playbook = f"{playbook}\n\n{self.index_des_skills}"
        if self.knowledge_index:
            playbook = f"{playbook}\n\n{self.knowledge_index}"
        return f"{playbook}\n\n---\n\n{task}"

    def _remaining(self, budget_seconds: int) -> float:
        elapsed = time.monotonic() - self.started
        return max(30.0, budget_seconds - elapsed)

    async def _demander_le_jeton_git(
        self, client: InternalClient, journal: EventJournal, stage_input: StageInput
    ) -> None:
        """Le jeton git du run, gardé en mémoire (S22-07). Sans lui, un dépôt public se clone encore."""
        if stage_input.repo is None:
            return
        try:
            self.jeton_git = await client.fetch_git_token()
        except Exception as exc:
            await journal.record("git.token", {"ok": False, "error": str(exc)[:300]})

    async def _publish(
        self,
        client: InternalClient,
        journal: EventJournal,
        workspace: Workspace,
        stage_input: StageInput,
        result: StageResult,
        base: str,
        depart: str = "",
    ) -> StageResult:
        """Commit, push, transcript, puis dépôt du résultat (étapes 11 et 12)."""
        await self._deliver(client, journal, workspace, stage_input, result)
        commit_message = _commit_message(stage_input, result)
        sha = await workspace.commit(commit_message)
        commits = await workspace.commits_since(base)
        additions, deletions = await workspace.diff_stats(base)
        result.artifacts.commits = commits or result.artifacts.commits
        if stage_input.repo:
            result.artifacts.branch = stage_input.repo.work_branch
            if depart:
                # Le commit où ce run a commencé : la garde de périmètre mesure depuis lui (S22-11),
                # pas depuis la base, qui compterait le travail des étapes d'avant.
                result.artifacts.reports[RAPPORT_DEPART] = depart
        result.evidence.diff_lines = additions + deletions
        result.evidence.diff_files = len(await workspace.changed_files(base))
        # Le diff, c'est git qui le dit, pas l'agent : mesuré (ADR 0045).
        result.evidence.measured = sorted({*(result.evidence.measured or []), "diff_files", "diff_lines"})

        # On pousse dès qu'il y a QUELQUE CHOSE à pousser, pas seulement quand c'est nous
        # qui avons commité. L'agent a git sous la main : quand il commite lui-même, l'arbre
        # est propre, `commit()` ne rend rien, et le travail restait dans un pod qui
        # disparaît — l'étape suivante trouvait alors le dépôt inchangé et concluait que
        # rien n'avait été fait.
        if (sha or commits) and stage_input.repo and not self.settings.dry_run:
            push = await workspace.push(stage_input.repo.work_branch, token=self.jeton_git)
            await journal.record("git.push", {"ok": push.ok, "output": push.output[-500:]})
            if not push.ok:
                # Une poussée ratée est la fin silencieuse la plus coûteuse : le résultat dit
                # « fait », et l'étape suivante ne trouve rien.
                result.summary = f"{result.summary} (push refused: {push.output[-200:]})"

        result.artifacts.context_pack_url = stage_input.context_pack_url
        # The whole result goes into the journal BEFORE it is posted (#410): the journal is what
        # the API already holds, so when the post fails the orchestrator still finds the result
        # there. The Tekton `result-url` pointed at a `result.json` nothing uploaded.
        await journal.record(
            "run.result",
            {
                "status": str(result.status),
                "summary": result.summary,
                "stage_result": result.model_dump(mode="json", by_alias=True),
            },
        )
        await journal.flush()
        # No transcript URL is announced: nothing uploads `transcript.jsonl`, and a link that
        # leads nowhere is worse than none. The API serves the transcript from the journal it
        # already holds (S36-01).
        result.artifacts.transcript_url = None
        write_result(workspace.result_path(), result)

        try:
            await client.post_result(result)
        except Exception as exc:  # the orchestrator reads the result from the journal instead
            await journal.record("result.post_failed", {"error": str(exc)[:300]})
            await journal.flush()
        return result


def _files(stage_input: StageInput) -> list[str]:
    """The files a `done` result must leave in `.choregos/outputs/` (ADR 0049)."""
    return list(expected_files(stage_input).values())


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
    summary = result.summary.strip().splitlines()[0][:100] if result.summary else "Choregos step"
    return f"{prefix}({scope}): {summary}"


def _diagnostic_sans_resultat(agent: Any, erreur: str | None, result_path: Path) -> tuple[str, str]:
    """Deux échecs qui se ressemblent et ne se soignent pas pareil : un agent qui a travaillé
    mais n'a pas écrit son résultat, et un agent dont le tour revient VIDE — clé refusée,
    quota atteint, fournisseur en panne. Le second accusé d'un oubli envoie l'enquête du
    mauvais côté."""
    if _agent_muet(agent.outcome) and not result_path.exists():
        # Ce que l'agent a écrit sur sa sortie d'erreur vaut mieux que toute reformulation :
        # c'est là que le fournisseur dit « quota atteint » ou « clé refusée ».
        bruit = " | ".join(agent.outcome.errors[-2:] or agent.stderr_tail[-2:])
        detail = (
            "the agent produced neither text nor a tool call: check its access to the model "
            f"(end of turn: {agent.outcome.stop_reason})" + (f" — {bruit[:300]}" if bruit else "")
        )
        return "agent_silencieux", detail
    return "invalid_result", (erreur or "unreadable result")


async def _turn(agent: Any, text: str, timeout: float) -> str | None:
    """One prompt turn; then the gateway's budget refusal, if the agent met one (S27-01)."""
    await agent.prompt(text, timeout=timeout)
    return _budget_refusal(agent)


def _budget_refusal(agent: Any) -> str | None:
    """The gateway's budget refusal, as the agent relayed it — an error, its stderr, or its text."""
    outcome: PromptOutcome = agent.outcome
    return gateway_budget_refusal(outcome.errors, agent.stderr_tail[-20:], outcome.said)


def _stop(
    agent: Any, refused: str | None, result: StageResult | None, error: str | None, result_path: Path
) -> tuple[Exit, str, str] | None:
    """Why the run ends without the agent's own result — `(exit code, reason, detail)` — or `None`.

    A budget refusal comes first: the agent's silence, or its failure, is then the gateway's doing,
    and `reason="budget"` is what keeps the workflow from retrying it with a fresh key."""
    if refused and (result is None or result.status is StageStatus.FAILED):
        return Exit.BUDGET_EXCEEDED, BUDGET_REASON, f"the gateway refused the run's key: {refused}"
    if result is None:
        raison, detail = _diagnostic_sans_resultat(agent, error, result_path)
        return Exit.INVALID_RESULT, raison, detail
    return None


def _agent_muet(outcome: PromptOutcome) -> bool:
    """Ni un mot, ni un outil : le modèle n'a pas répondu.

    Un agent qui a parlé sans écrire son résultat est un agent distrait — on le lui
    rappelle. Un agent dont le tour revient VIDE n'a rien à se faire rappeler : sa clé est
    refusée, son quota est atteint, ou son fournisseur est en panne.

    L'appelant y ajoute l'absence de tout fichier de résultat : un agent qui en a écrit un,
    même illisible, a agi — le lui rappeler a du sens.
    """
    return outcome.messages == 0 and outcome.tool_calls == 0


def _agent_exit(stop_reason: str, cancelled: bool) -> str:
    if stop_reason in {"max_turns", "loop"}:
        return "limit"
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


#: Where a run keeps its agent's conversation id, on the workspace (S35-03).
SAVED_SESSION = Path(".choregos") / "acp-session.json"


async def _open_session(
    agent: Any, journal: EventJournal, workspace: Path, backend: str, mcp_servers: list[dict[str, Any]]
) -> None:
    """A new conversation — or, for a run resumed in its own sandbox after a person's answer
    (S35-03), the SAME one, when the agent can reload it (ACP `session/load`).

    The conversation id is saved on the workspace volume when it is opened; the agent keeps the
    conversation itself in its home, which is the workspace (`HOME=/workspace` in the runner
    image), so both survive a suspended sandbox. An agent that cannot reload says so in the
    journal, and the run goes on in a new conversation, in the kept workspace, with the answer.
    """
    saved = _saved_session(workspace)
    if saved is not None and saved.get("backend") == backend:
        if agent.can_load_session:
            try:
                await agent.load_session(str(saved["session_id"]), str(workspace), mcp_servers)
            except Exception as exc:
                await journal.record("session/load.failed", {"backend": backend, "error": str(exc)[:300]})
            else:
                await journal.record("session/load", {"backend": backend, "session_id": saved["session_id"]})
                return
        else:
            await journal.record(
                "session/load.unsupported",
                {
                    "backend": backend,
                    "detail": "the agent does not announce `loadSession`: its conversation is not "
                    "reloaded; a new one starts in the kept workspace, with the answer",
                },
            )
    session_id = await agent.new_session(str(workspace), mcp_servers)
    await journal.record("session/new", {"backend": backend})
    path = workspace / SAVED_SESSION
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"backend": backend, "session_id": session_id}), encoding="utf-8")


def _saved_session(workspace: Path) -> dict[str, Any] | None:
    path = workspace / SAVED_SESSION
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("session_id") else None


def _events_already_journaled(path: Path) -> int:
    """A run resumed in its own sandbox after a person's answer (S35-03) finds its journal on the
    workspace volume: it goes on from there, or the API, idempotent by `seq`, would drop every
    event of the continuation as already received. A fresh workspace has none."""
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as lines:
        return sum(1 for _ in lines)


async def run_stage(settings: RunnerSettings | None = None) -> RunOutcome:
    """Point d'entrée programmatique (utilisé par la CLI et les tests)."""
    return await Runner(settings).run()


def main_async(settings: RunnerSettings | None = None) -> RunOutcome:
    return asyncio.run(run_stage(settings))
