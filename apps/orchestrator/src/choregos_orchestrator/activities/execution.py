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

from choregos_api.db.models import Run, WorkItem
from choregos_api.security import mint_run_token
from choregos_api.services import persist_event
from choregos_contracts import EventType, StageInput, StageResult, StageStatus
from choregos_core import ExecRef, StageJobSpec, utcnow
from temporalio import activity

from ..config import get_settings
from ..gitops import PLAGES_PRIVEES
from .base import db, project_bundle


def _runner_namespace(settings: Any, slug: str) -> str:
    """Où vivent les pods d'agent d'un projet. Une seule règle, lue partout."""
    return str(settings.runner_namespace_pattern).format(slug=slug)


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
        env.setdefault("NO_PROXY", ",".join(("localhost,127.0.0.1,.svc,.cluster.local", *PLAGES_PRIVEES[:3])))
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


@activity.defn(name="start_run")
async def start_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Démarre l'exécution (Tekton, Job K8s, Docker local, fake) — idempotent par `run_id`."""
    settings = get_settings()
    stage_input = StageInput.model_validate(payload["stage_input"])
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        run = await session.get(Run, stage_input.run_id)
        if run is not None and run.executor_ref:
            return {"executor_ref": run.executor_ref, "kind": run.executor_kind, "reused": True}
        spec = StageJobSpec(
            run_id=stage_input.run_id,
            project_slug=bundle.slug,
            namespace=_runner_namespace(settings, bundle.slug),
            runner_image=settings.runner_image,
            api_url=stage_input.callbacks.api_url,
            run_token=stage_input.callbacks.run_token,
            stage_input=stage_input,
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
        }


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
            if run is not None and run.result is not None:
                return {"status": "finished", "result": run.result}
            ref = ExecRef(
                kind=(run.executor_kind if run and run.executor_kind else "fake"),
                name=(run.executor_ref if run and run.executor_ref else run_id),
                # Le MÊME namespace qu'au démarrage : sans lui, l'exécuteur Kubernetes
                # interrogeait `/namespaces/None/jobs/…` et recevait un 403 dont le message
                # parle de droits, jamais du namespace manquant.
                namespace=_runner_namespace(settings, bundle.slug),
                run_id=run_id,
            )
            status = await bundle.adapters.executor.status(ref)
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
                if run is not None and run.result is not None:
                    return {"status": "finished", "result": run.result}
                # Le runner n'a pas pu poster : on lit le résultat publié par l'exécuteur
                # (`result-url` côté Tekton), et on l'enregistre nous-mêmes.
                fetcher = getattr(bundle.adapters.executor, "fetch_result", None)
                fetched = await fetcher(ref) if fetcher is not None else None
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
                        summary="le runner s'est terminé sans poster de résultat",
                        reason="invalid_result",
                    ).model_dump(mode="json", by_alias=True),
                }
            return {
                "status": status.state,
                "result": StageResult(
                    status=StageStatus.FAILED,
                    summary=status.message or f"exécution {status.state}",
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
                            f"jamais admis : {en_file / 60:.0f} min en file d'attente "
                            f"({status.message or 'plafond atteint'})"
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
            status=StageStatus.FAILED, summary=f"dépassement de {timeout_minutes:g} min", reason="timeout"
        ).model_dump(mode="json", by_alias=True),
    }


async def _abandonner(project_id: str, run_id: str, ref: ExecRef) -> None:
    """Retire le Job (ou l'équivalent) d'un run qu'on n'attend plus, sans jamais lever :
    ce qui compte est que le run se termine, le nettoyage est un devoir, pas une condition."""
    with contextlib.suppress(Exception):
        async with db() as session:
            bundle = await project_bundle(session, project_id)
            await bundle.adapters.executor.cancel(ref)
            run = await session.get(Run, run_id)
            if run is not None and run.status in {"queued", "running"}:
                run.status = "failed"
                run.ended_at = utcnow()


@activity.defn(name="cancel_run")
async def cancel_run(payload: dict[str, Any]) -> None:
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
        await bundle.adapters.executor.cancel(ref)
        run.status = "cancelled"
        run.ended_at = utcnow()
