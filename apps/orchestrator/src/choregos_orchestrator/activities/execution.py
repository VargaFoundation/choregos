# SPDX-License-Identifier: Apache-2.0
"""Activités d'exécution d'une étape : lancer le run, l'attendre, l'abandonner.

C'est la moitié de `stage` qui parle à l'exécuteur (Tekton, Job Kubernetes, Docker local,
fake) ; toutes idempotentes par `run_id`. La préparation (modèle, clé, contexte) reste dans
`stage`, le bilan (coût, résultat, findings) dans `bilan`.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from choregos_adapters.executor.log_archive import archive_run_logs
from choregos_api.db.models import Run, RunEvent, WorkItem
from choregos_api.security import mint_run_token
from choregos_api.services import persist_event
from choregos_contracts import EventType, StageInput, StageResult, StageStatus
from choregos_core import ExecRef, ExecStatus, StageJobSpec, utcnow
from choregos_core.redaction import REDACTED
from sqlalchemy import select
from temporalio import activity

from ..config import get_settings
from ..gitops import PLAGES_PRIVEES
from .base import db, project_bundle, without_secrets
from .bilan import close_run_spend
from .run_key import mint_run_key


def _runner_namespace(settings: Any, slug: str) -> str:
    """Où vivent les pods d'agent d'un projet. Une seule règle, lue partout."""
    return str(settings.runner_namespace_pattern).format(slug=slug)


def hotes_a_joindre_en_direct(settings: Any) -> list[str]:
    """Les hôtes que le runner doit joindre SANS passer par le proxy d'egress.

    Ce ne sont pas des hôtes devinés : ce sont ceux que la plateforme lui donne elle-même —
    l'URL de rappel de l'API interne, la passerelle de modèles, la mémoire. Les lire ici rend
    la promesse de `NO_PROXY` vraie par construction au lieu de reposer sur un suffixe.

    Le suffixe ne suffisait pas. `.svc` couvre `choregos-api.choregos.svc`, PAS `choregos-api`,
    et c'est précisément la forme courte que la plateforme fabrique elle-même
    (`callback_url` → `http://choregos-api:8000`). Sur le locataire dev du 2026-09-27, le
    premier appel du runner — chercher son `StageInput` — partait donc vers un proxy d'egress
    qui n'existait pas dans ce namespace, et mourait sur « Name or service not known ». Le
    message ne nommait ni l'URL, ni le proxy : le runner concluait « StageInput introuvable ».
    """
    hotes: list[str] = []
    for url in (
        getattr(settings, "callback_url", ""),
        getattr(settings, "gateway_url", ""),
        getattr(settings, "memory_url", ""),
    ):
        hote = urlsplit(str(url or "")).hostname
        if hote and hote not in hotes:
            hotes.append(hote)
    return hotes


def env_du_runner(settings: Any, namespace: str) -> dict[str, str]:
    """L'environnement d'un pod d'agent : ce que le déploiement passe, plus le proxy d'egress.

    `git`, `pip`, `npm`, `uv` et l'agent lisent `HTTP_PROXY`/`HTTPS_PROXY` ; `NO_PROXY`
    garde le trafic du cluster (API interne, passerelle, mémoire) en direct — le proxy ne
    sert qu'à sortir. Sans `runner_egress_proxy`, rien n'est ajouté.
    """
    env = dict(settings.runner_env)
    proxy = str(getattr(settings, "runner_egress_proxy", "") or "").format(namespace=namespace)
    if proxy:
        env.setdefault("HTTP_PROXY", proxy)
        env.setdefault("HTTPS_PROXY", proxy)
        env.setdefault(
            "NO_PROXY",
            ",".join(
                (
                    "localhost",
                    "127.0.0.1",
                    ".svc",
                    ".cluster.local",
                    *hotes_a_joindre_en_direct(settings),
                    *PLAGES_PRIVEES[:3],
                )
            ),
        )
        for nom in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            env[nom.lower()] = env[nom]
    return env


def classe_d_execution(settings: Any, policy: Any) -> str | None:
    """La `RuntimeClass` d'un pod d'agent : celle qu'exige la politique, sinon celle du
    déploiement, sinon aucune. Une politique `gvisor` ne se contourne pas par la valeur."""
    if policy.sandbox.runtime == "gvisor":
        return "gvisor"
    return str(getattr(settings, "runner_runtime_class", "") or "") or None


#: Combien de temps un run peut attendre une place avant d'être abandonné — six heures :
#: une nuit de pointe, pas une éternité.
FILE_MAX_MINUTES = 360


async def archive_once(
    executor: Any, run: Run | None, ref: ExecRef, status: ExecStatus | None = None
) -> None:
    """Archive how the run's pod ended and the tail of its log, redacted, once per run (S36-02).

    Called on every way a run ends — result posted, pod finished, timed out, abandoned in the
    queue, cancelled — and always BEFORE the executor is told to cancel, because deleting a Job
    deletes its pods and their logs. Only the `Executor` protocol is used (`status`, `logs`), so a
    new executor gets its runs archived without a line here.

    Idempotent (ADR 0008): the first archive stays; a replayed activity finds it and does nothing.
    Never raises: a run must end even when its logs cannot be read, and the archive says why.
    """
    if run is None or _archived(run):
        return
    with contextlib.suppress(Exception):
        archive = await archive_run_logs(executor, ref, status)
        run.log_archive = archive.model_dump(mode="json")


async def result_from_journal(session: Any, run_id: str) -> StageResult | None:
    """The `StageResult` the runner journaled before posting it (#410), when the post failed.

    The last `run.result` event carrying a `stage_result` wins; one that does not validate is
    ignored rather than trusted. `None` when the runner never got that far.
    """
    rows = (
        (
            await session.execute(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.type == "run.result")
                .order_by(RunEvent.seq.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        payload = (row.payload or {}).get("stage_result")
        if isinstance(payload, dict):
            with contextlib.suppress(ValueError):
                return StageResult.model_validate(payload)
    return None


def _archived(run: Run) -> bool:
    """A final archive exists; a live snapshot (`final: false`) does not count."""
    return run.log_archive is not None and bool(run.log_archive.get("final", True))


async def snapshot_live(executor: Any, run: Run | None, ref: ExecRef, status: ExecStatus) -> None:
    """While the pod runs, refresh the live tail of its log at each poll (S36-03).

    The API never talks to the cluster: it serves this snapshot, which is as fresh as the poll
    interval (`heartbeat_seconds`). Reading pod logs stays a right of the orchestrator alone.
    A final archive is never overwritten. Never raises.
    """
    if run is None or status.state != "running" or _archived(run):
        return
    with contextlib.suppress(Exception):
        snapshot = await archive_run_logs(executor, ref, status, final=False)
        run.log_archive = snapshot.model_dump(mode="json")


def _capabilities_for(executor: Any, ref: ExecRef, default: list[str]) -> list[str]:
    """What the executor that took THIS run can do: a routing executor has two, the second one lesser."""
    capabilities_for = getattr(executor, "capabilities_for", None)
    return sorted(capabilities_for(ref)) if capabilities_for is not None else default


def _capabilities_of_run(executor: Any, run: Run, namespace: str) -> list[str]:
    ref = ExecRef(
        kind=run.executor_kind or "fake", name=run.executor_ref or run.id, namespace=namespace, run_id=run.id
    )
    return _capabilities_for(executor, ref, sorted(getattr(executor, "capabilities", frozenset())))


@activity.defn(name="start_run")
async def start_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Démarre l'exécution (Tekton, Job K8s, Docker local, fake) — idempotent par `run_id`."""
    settings = get_settings()
    stage_input = StageInput.model_validate(payload["stage_input"])
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        run = await session.get(Run, stage_input.run_id)
        # What the executor can do, for the workflow to decide without assuming (ADR 0013):
        # `suspend` and `resume` make it keep a sandbox across a person's answer (S35-03).
        capabilities = sorted(getattr(bundle.adapters.executor, "capabilities", frozenset()))
        if run is not None and run.executor_ref:
            # A run on the second executor (S40-03c) does what THAT executor does.
            capabilities = _capabilities_of_run(
                bundle.adapters.executor, run, _runner_namespace(settings, bundle.slug)
            )
            return {
                "executor_ref": run.executor_ref,
                "kind": run.executor_kind,
                "reused": True,
                "capabilities": capabilities,
            }
        # The run token lives in the run's Secret only (#519): minted here, never stored, never
        # carried by a Temporal payload. A payload of an older history may still bring one.
        run_token = stage_input.callbacks.run_token
        if not run_token or run_token == REDACTED:
            run_token = mint_run_token(
                stage_input.run_id,
                project_slug=bundle.slug,
                work_item_key=stage_input.work_item.key,
                ttl_minutes=stage_input.budget.max_minutes + 15,
            )
        gateway_key = await _gateway_key(session, bundle, run, stage_input)
        spec = StageJobSpec(
            run_id=stage_input.run_id,
            project_slug=bundle.slug,
            namespace=_runner_namespace(settings, bundle.slug),
            # A job names its image (ADR 0056); prepare_stage resolved it against `job_images`.
            runner_image=(stage_input.job.image if stage_input.job else None) or settings.runner_image,
            api_url=stage_input.callbacks.api_url,
            run_token=run_token,
            gateway_key=gateway_key,
            stage_input=stage_input.model_copy(update={"gateway_key": None}),
            timeout_minutes=stage_input.budget.max_minutes + 10,
            runtime_class=classe_d_execution(settings, bundle.policy),
            env=env_du_runner(settings, _runner_namespace(settings, bundle.slug)),
            labels={
                "choregos/project": bundle.slug,
                "choregos/run-id": stage_input.run_id,
                "choregos/work-item": stage_input.work_item.key.replace("/", "_").replace("#", "-"),
            },
        )
        ref = await bundle.adapters.executor.start(spec)
        capabilities = _capabilities_for(bundle.adapters.executor, ref, capabilities)
        # Un exécuteur qui sait mettre en file (`queue`) a pu créer le run SANS pod, parce
        # que le plafond de simultanéité était atteint. Le marquer « running » ferait
        # mentir le board, la page du run et les mesures : rien ne tourne. On lui demande
        # donc son état, et on ne pose la question qu'à ceux qui peuvent répondre.
        en_file = False
        motif = ""
        if "queue" in getattr(bundle.adapters.executor, "capabilities", frozenset()):
            etat = await bundle.adapters.executor.status(ref)
            en_file = etat.state == "pending" and bool(etat.message)
            motif = etat.message if en_file else ""
        if run is not None:
            run.executor_kind = str(ref.kind)
            run.executor_ref = ref.name
            run.status = "queued" if en_file else "running"
        await persist_event(
            session,
            EventType.RUN_QUEUED if en_file else EventType.RUN_STARTED,
            project_id=bundle.project.id,
            work_item_id=payload["work_item_id"],
            project_slug=bundle.slug,
            subject=stage_input.run_id,
            run_id=stage_input.run_id,
            executor=str(ref.kind),
            **({"reason": motif} if en_file else {}),
        )
        return {
            "executor_ref": ref.name,
            "kind": str(ref.kind),
            "namespace": ref.namespace,
            "reused": False,
            "queued": en_file,
            "capabilities": capabilities,
        }


async def _gateway_key(session: Any, bundle: Any, run: Run | None, stage_input: StageInput) -> str | None:
    """The run's gateway key, for its Secret only (#523): minted now, never stored on the row.

    A run prepared before #523 already has one: on its row (S22-37) or in the payload of an older
    history (before #519). That key is used, and the row's copy leaves the row — the runner reads
    it from its Secret now.

    A job (ADR 0056) calls no model: it gets no key, minted or legacy.
    """
    if stage_input.job is not None:
        return None
    if run is None:
        return stage_input.gateway_key
    legacy = stage_input.gateway_key or (run.stage_input or {}).get("gateway_key")
    if legacy:
        if run.stage_input and run.stage_input.get("gateway_key") is not None:
            run.stage_input = without_secrets(run.stage_input, keep_gateway_key=False)
        return str(legacy)
    return await mint_run_key(session, bundle, run, stage_input)


@activity.defn(name="await_run")
async def await_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Attend la fin du run, avec heartbeat et annulation propre (S1-03)."""
    settings = get_settings()
    run_id = payload["run_id"]
    timeout_minutes = float(payload.get("timeout_minutes", 120))
    deadline = timedelta(minutes=timeout_minutes).total_seconds()
    # Le temps passé EN FILE ne compte pas contre le budget de l'étape : le banc du
    # 2026-09-25 a vu deux runs RH « dépasser 20 min » sans qu'un pod ait jamais tourné —
    # leur Job attendait une place. La file a sa propre borne, bien plus large.
    file_max = timedelta(
        minutes=float(payload.get("queue_timeout_minutes", FILE_MAX_MINUTES))
    ).total_seconds()
    waited = 0.0
    en_file = 0.0
    interval = float(payload.get("poll_seconds", settings.heartbeat_seconds))
    a_tourne = False
    while waited <= deadline:
        async with db() as session:
            bundle = await project_bundle(session, payload["project_id"])
            run = await session.get(Run, run_id)
            ref = ExecRef(
                kind=(run.executor_kind if run and run.executor_kind else "fake"),
                name=(run.executor_ref if run and run.executor_ref else run_id),
                # Le MÊME namespace qu'au démarrage : sans lui, l'exécuteur Kubernetes
                # interrogeait `/namespaces/None/jobs/…` et recevait un 403 dont le message
                # parle de droits, jamais du namespace manquant.
                namespace=_runner_namespace(settings, bundle.slug),
                run_id=run_id,
            )
            if run is not None and run.result is not None:
                await archive_once(bundle.adapters.executor, run, ref)
                return {"status": "finished", "result": run.result}
            status = await bundle.adapters.executor.status(ref)
            await snapshot_live(bundle.adapters.executor, run, ref, status)
            # Tant qu'il attend, son jeton vieillit. Minté à la préparation pour
            # `max_minutes + 15`, il expire PENDANT l'attente si la file est longue, et
            # l'agent démarre pour recevoir « Signature has expired » — vu sur le banc du
            # 2026-09-24. On le remplace à chaque relevé, tant que rien n'a démarré.
            if (
                run is not None
                and run.status == "queued"
                and status.state == "pending"
                and "renew" in getattr(bundle.adapters.executor, "capabilities", frozenset())
            ):
                item_du_run = await session.get(WorkItem, run.work_item_id)
                renew = getattr(bundle.adapters.executor, "renew", None)
                if renew is not None and item_du_run is not None:
                    # Un renouvellement raté ne tue pas le run : il peut encore démarrer à
                    # temps, et s'il n'y arrive pas il échouera sur SON message à lui.
                    # Un droit absent doit dégrader, pas détruire — c'est la deuxième fois
                    # que cette leçon se paie (banc du 2026-09-24).
                    with contextlib.suppress(Exception):
                        await renew(
                            ref,
                            mint_run_token(
                                run_id,
                                project_slug=bundle.slug,
                                work_item_key=item_du_run.tracker_key,
                                # De quoi couvrir ce qu'il reste d'attente, plus l'étape.
                                ttl_minutes=int(timeout_minutes) + 15,
                            ),
                        )
            # Le run attendait une place et vient de l'obtenir : sans cette bascule, il
            # resterait « en file » jusqu'à sa fin, alors qu'il tourne. C'est ici que ça
            # se voit, parce que c'est ici qu'on interroge l'exécuteur.
            if run is not None and run.status == "queued" and status.state == "running":
                run.status = "running"
                run.started_at = run.started_at or utcnow()
                await persist_event(
                    session,
                    EventType.RUN_STARTED,
                    project_id=bundle.project.id,
                    work_item_id=run.work_item_id,
                    project_slug=bundle.slug,
                    subject=run_id,
                    run_id=run_id,
                    executor=str(ref.kind),
                )
        if status.finished:
            async with db() as session:
                bundle = await project_bundle(session, payload["project_id"])
                run = await session.get(Run, run_id)
                await archive_once(bundle.adapters.executor, run, ref, status)
                if run is not None and run.result is not None:
                    return {"status": "finished", "result": run.result}
                # Le runner n'a pas pu poster : on lit le résultat publié par l'exécuteur
                # (`result-url` côté Tekton), et on l'enregistre nous-mêmes.
                # First from the run's journal, which the API already holds (#410): the runner
                # journals the whole result before posting it, on every executor.
                fetched = await result_from_journal(session, run_id)
                fetcher = getattr(bundle.adapters.executor, "fetch_result", None)
                if fetched is None and fetcher is not None:
                    fetched = await fetcher(ref)
                if fetched is not None:
                    payload_result = fetched.model_dump(mode="json", by_alias=True)
                    if run is not None:
                        run.result = payload_result
                    return {"status": "finished", "result": payload_result}
            if status.state == "succeeded":
                return {
                    "status": "missing_result",
                    "result": StageResult(
                        status=StageStatus.FAILED,
                        summary="the runner ended without posting a result",
                        reason="invalid_result",
                    ).model_dump(mode="json", by_alias=True),
                }
            return {
                "status": status.state,
                "result": StageResult(
                    status=StageStatus.FAILED,
                    summary=status.message or f"run {status.state}",
                    reason=status.state,
                ).model_dump(mode="json", by_alias=True),
            }
        with contextlib.suppress(RuntimeError):  # hors contexte Temporal (tests unitaires)
            activity.heartbeat({"run_id": run_id, "waited_s": waited, "en_file_s": en_file})
        await asyncio.sleep(interval)
        if status.state == "pending" and not a_tourne:
            en_file += interval
            if en_file > file_max:
                await _abandonner(payload["project_id"], run_id, ref)
                return {
                    "status": "queue_timed_out",
                    "result": StageResult(
                        status=StageStatus.FAILED,
                        summary=(
                            f"never admitted: {en_file / 60:.0f} min in the queue "
                            f"({status.message or 'limit reached'})"
                        ),
                        reason="queue_timeout",
                    ).model_dump(mode="json", by_alias=True),
                }
            continue
        a_tourne = True
        waited += interval
    # Un run qui dépasse son budget ne doit rien laisser derrière lui : un Job suspendu
    # dont plus personne ne relève l'état reste en tête de file et bloque tous les suivants
    # — vu sur le banc du 2026-09-25, cinq tickets figés derrière deux runs morts.
    await _abandonner(payload["project_id"], run_id, ref)
    return {
        "status": "timed_out",
        "result": StageResult(
            status=StageStatus.FAILED, summary=f"over {timeout_minutes:g} min", reason="timeout"
        ).model_dump(mode="json", by_alias=True),
    }


async def _abandonner(project_id: str, run_id: str, ref: ExecRef) -> None:
    """Retire le Job (ou l'équivalent) d'un run qu'on n'attend plus, sans jamais lever :
    ce qui compte est que le run se termine, le nettoyage est un devoir, pas une condition."""
    with contextlib.suppress(Exception):
        async with db() as session:
            bundle = await project_bundle(session, project_id)
            run = await session.get(Run, run_id)
            # Before the cancel: deleting the Job deletes its pods, and their logs with them.
            await archive_once(bundle.adapters.executor, run, ref)
            await bundle.adapters.executor.cancel(ref)
            if run is not None and run.status in {"queued", "running"}:
                run.status = "failed"
                run.ended_at = utcnow()


async def abandon_queued_runs(project_id: str, run_ids: list[str]) -> None:
    """Removes, at the executor, the runs among these that still wait for a slot (#501).

    A queued run is a suspended Job (`k8s_job`) or a Secret marked `choregos/queued`
    (`agent_sandbox`). Nothing ends it on its own: a suspended Job never finishes, so its
    `ttlSecondsAfterFinished` never starts, and Kubernetes stops `activeDeadlineSeconds` while a
    Job is suspended. Only `await_run` gave it a bound (`queue_timeout_minutes`); once the
    interpreter dies, nobody polls it, and the Job and its Secret (token, gateway key) stayed
    forever. Deleting the Job (propagation `Background`) takes the Secret it owns with it.

    Only runs the executor knows (`executor_ref`) and that never ran (`queued`): a running run
    ends on its own deadline, and its TTL then collects it. Never raises.
    """
    for run_id in run_ids:
        with contextlib.suppress(Exception):
            async with db() as session:
                bundle = await project_bundle(session, project_id)
                run = await session.get(Run, run_id)
                if run is None or run.status != "queued" or not run.executor_ref:
                    continue
                ref = ExecRef(
                    kind=run.executor_kind or "fake",
                    name=run.executor_ref,
                    namespace=_runner_namespace(get_settings(), bundle.slug),
                    run_id=run.id,
                )
            await _abandonner(project_id, run_id, ref)


@activity.defn(name="cancel_run")
async def cancel_run(payload: dict[str, Any]) -> None:
    """Stops a run nobody will wait for — its interpreter was cancelled — and closes its key.

    Replayable (ADR 0008): cancelling what is gone is not an error, and the spend is booked once.
    Its key is revoked and scrubbed after the executor is told to stop (#523), never before: a
    revoked key may no longer tell what it spent.
    """
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        run = await session.get(Run, payload["run_id"])
        if run is None:
            return
        ref = ExecRef(
            kind=run.executor_kind or "fake",
            name=run.executor_ref or run.id,
            namespace=_runner_namespace(get_settings(), bundle.slug),
            run_id=run.id,
        )
        await archive_once(bundle.adapters.executor, run, ref)
        with contextlib.suppress(Exception):  # gone already, or never started
            await bundle.adapters.executor.cancel(ref)
        if run.status in {"queued", "running"}:  # a run that ended keeps how it ended
            run.status = "cancelled"
        run.ended_at = run.ended_at or utcnow()
    await close_run_spend(payload["project_id"], payload["run_id"])
