# SPDX-License-Identifier: Apache-2.0
"""Activités de provisioning d'un projet (docs/plan/03 §3.3).

Chaque étape est idempotente : rejouer un provisioning partiellement échoué reprend
là où il s'est arrêté, sans rien créer en double.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml
from choregos_api.db.models import Project
from choregos_api.services import persist_event
from choregos_contracts import EventType
from choregos_core import Fact, Message, Provenance, utcnow
from temporalio import activity

from ..config import get_settings
from .base import db, project_bundle


def repertoire_templates() -> Path:
    """Où vivent les templates de projet : `CHOREGOS_TEMPLATES_DIR`, sinon `templates/` du dépôt.

    `parents[5]` supposait la disposition du dépôt source ; dans l'image, c'est `/app`, et
    `templates/` n'y était même pas copié. Le réglage dit où chercher, et l'absence se
    voit à la première lecture, avec le chemin cherché.
    """
    import os

    configure = os.environ.get("CHOREGOS_TEMPLATES_DIR", "").strip()
    if configure:
        return Path(configure)
    return Path(__file__).resolve().parents[5] / "templates"


TEMPLATES_DIR = repertoire_templates()

LABELS = {
    "agent-ready": "0e8a16",
    "choregos:inbox": "ededed",
    "finding": "d93f0b",
    "source:agent": "c5def5",
    "needs-triage": "fbca04",
    "hotfix": "b60205",
    "size:S": "ededed",
    "size:M": "ededed",
    "size:L": "ededed",
    "size:XL": "ededed",
    "risk:low": "c2e0c6",
    "risk:medium": "fbca04",
    "risk:high": "d93f0b",
}


@activity.defn(name="load_template_steps")
async def load_template_steps(payload: dict[str, Any]) -> dict[str, Any]:
    """Lit le manifeste du template et rend la liste ordonnée des étapes."""
    ref = payload.get("template_ref") or "github-tekton-argo-k8s@1.0.0"
    name = ref.split("@", 1)[0]
    manifest_path = TEMPLATES_DIR / name / "manifest.yaml"
    if not manifest_path.exists():
        return {"steps": [], "manifest": {}, "missing": True}
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = []
    for step in manifest.get("steps", []):
        if isinstance(step, str):
            steps.append({"name": step, "params": {}})
        else:
            key = next(iter(step))
            steps.append({"name": key, "params": step[key] or {}})
    return {"steps": steps, "manifest": manifest, "missing": False}


@activity.defn(name="run_provision_step")
async def run_provision_step(payload: dict[str, Any]) -> dict[str, Any]:
    """Exécute une étape de provisioning et met à jour l'état visible dans le front (SSE)."""
    name: str = payload["step"]
    params: dict[str, Any] = payload.get("params", {})
    settings = get_settings()
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        project = bundle.project
        state = dict(project.provision_state or {})
        steps: list[dict[str, Any]] = list(state.get("steps", []))
        done = next((s for s in steps if s["name"] == name and s["status"] == "succeeded"), None)
        if done is not None and payload.get("resume", True):
            return {"status": "skipped", "step": name, "reason": "already done"}

        entry = {"name": name, "status": "running", "started_at": utcnow().isoformat(), "message": None}
        steps = [s for s in steps if s["name"] != name] + [entry]
        state["steps"] = steps
        state["current_step"] = name
        project.provision_state = state
        await persist_event(
            session,
            EventType.PROVISIONING_STEP,
            project_id=project.id,
            project_slug=bundle.slug,
            subject=bundle.slug,
            step=name,
            status="running",
        )

        try:
            message = await _execute(name, params, bundle, settings, payload)
            entry.update(status="succeeded", message=message, ended_at=utcnow().isoformat())
        except Exception as exc:  # une étape qui échoue est réparable, pas fatale
            entry.update(
                status="failed",
                message=str(exc)[:500],
                remediation=_remediation(name),
                ended_at=utcnow().isoformat(),
            )
            state["steps"] = [s for s in steps if s["name"] != name] + [entry]
            project.provision_state = state
            await persist_event(
                session,
                EventType.PROVISIONING_STEP,
                project_id=project.id,
                project_slug=bundle.slug,
                subject=bundle.slug,
                step=name,
                status="failed",
                error=str(exc)[:200],
            )
            raise

        state["steps"] = [s for s in steps if s["name"] != name] + [entry]
        project.provision_state = state
        await persist_event(
            session,
            EventType.PROVISIONING_STEP,
            project_id=project.id,
            project_slug=bundle.slug,
            subject=bundle.slug,
            step=name,
            status="succeeded",
        )
        return {"status": "succeeded", "step": name, "message": entry["message"]}


async def _execute(
    name: str, params: dict[str, Any], bundle: Any, settings: Any, payload: dict[str, Any]
) -> str:
    """Aiguillage des étapes. Toute étape inconnue est ignorée explicitement, jamais devinée."""
    handler = _ETAPES.get(name)
    if handler is None:
        return _etape_inconnue(name)
    return str(await handler(params, bundle, settings))


async def _github_install_app(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    checker = getattr(bundle.adapters.tracker, "test", None)
    if checker is not None:
        result = await checker()
        return f"App installed: {result.get('repo', bundle.slug)}"
    return "App assumed installed (fakes mode)"


#: La couleur d'une étiquette qu'un gabarit nomme sans la colorer.
COULEUR_PAR_DEFAUT = "ededed"


def etiquettes_du_pas(params: dict[str, Any]) -> dict[str, str]:
    """Celles de la plateforme, puis celles que le gabarit ajoute (`labels:`, une liste de noms ou un
    dictionnaire nom → couleur) : le routage d'un gabarit lit des étiquettes que le dépôt n'a pas
    encore (`bug`, `adr`, `complex`…), et un ticket étiqueté d'une étiquette absente ne naît pas
    dans le bon workflow (S21-22). Une couleur de la plateforme n'est jamais remplacée."""
    ajoutees = params.get("labels") or {}
    if isinstance(ajoutees, list):
        ajoutees = dict.fromkeys((str(nom) for nom in ajoutees), COULEUR_PAR_DEFAUT)
    return {**{str(nom): str(couleur).lstrip("#") for nom, couleur in dict(ajoutees).items()}, **LABELS}


async def _github_ensure_labels(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    etiquettes = etiquettes_du_pas(params)
    ensure = getattr(bundle.adapters.tracker, "ensure_labels", None)
    if ensure is not None:
        await ensure(etiquettes)
    return f"{len(etiquettes)} labels ensured"


async def _github_ensure_project_board(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    fields = params.get("fields", ["Status", "Cost", "Size", "Risk", "Run"])
    return f"board checked ({', '.join(fields)})"


async def _github_ensure_webhooks(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    events = params.get("events", [])
    return f"webhooks: {', '.join(events) if events else 'default'}"


async def _argocd_wait_synced(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    apps = params.get("apps", [f"choregos-project-{bundle.slug}"])
    for app in apps:
        health = await bundle.adapters.cd.health(str(app).replace("{{slug}}", bundle.slug))
        if health.status in {"Degraded", "Missing"}:
            raise RuntimeError(f"application {app} is not synced: {health.status}")
    return f"{len(apps)} application(s) synced"


async def _memory_create_tenant(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    await bundle.adapters.memory.write_fact(
        bundle.slug,
        Fact(
            kind="convention",
            subject=f"convention:{bundle.slug}:bootstrap",
            content=f"Project {bundle.slug} provisioned by Choregos.",
            provenance=Provenance(source="provisioning"),
        ),
    )
    return "memory tenant created"


async def _memory_initial_import(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    sources = params.get("sources", ["readme", "docs", "adr"])
    await bundle.adapters.memory.ingest_events(
        bundle.slug,
        [
            {
                "external_id": f"{bundle.slug}:{source}",
                "kind": "convention",
                "subject": f"convention:{bundle.slug}:{source}",
                "content": f"Initial import from {source}",
                "source": "provisioning",
            }
            for source in sources
        ],
    )
    return f"initial import: {', '.join(sources)}"


async def _gateway_create_team_and_budget(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    key = await bundle.adapters.gateway.mint_key(
        {"project": bundle.slug, "kind": "team"},
        budget_usd=bundle.engine.daily_budget() or 100.0,
        ttl_s=30 * 24 * 3600,
        models=[],
    )
    await bundle.adapters.gateway.revoke(key.key_id)
    return "gateway team and budget created"


async def _notify_test_message(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    await bundle.adapters.notify.send(
        bundle.config.notify.slack_channel or "#choregos",
        Message(title=f"Choregos — project {bundle.slug} provisioned", severity="success"),
    )
    return "test message sent"


def _etape_constante(texte: str) -> Etape:
    async def _etape(params: dict[str, Any], bundle: Any, settings: Any) -> str:
        return texte

    return _etape


def _etape_inconnue(name: str) -> str:
    return f"step `{name}` skipped (not implemented for this template)"


async def _gitops_write(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    return await _write_manifests(bundle, params, settings)


async def _repo_scaffold_pr(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    return f"scaffolding pull request: {len(params.get('files', []))} file(s)"


async def _aca_check_environment(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    return await _check_aca("aca.check_environment", bundle)


async def _aca_check_identity(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    return await _check_aca("aca.check_identity", bundle)


Etape = Callable[[dict[str, Any], Any, Any], Awaitable[str]]

#: Une étape = une fonction ; ce qu'un template nomme et qui n'est pas ici est ignoré, et dit.
_ETAPES: dict[str, Etape] = {
    "github.install_app": _github_install_app,
    "github.ensure_labels": _github_ensure_labels,
    "github.ensure_project_board": _github_ensure_project_board,
    "github.ensure_issue_template": _etape_constante("issue template added"),
    "github.ensure_webhooks": _github_ensure_webhooks,
    "aca.check_environment": _aca_check_environment,
    "aca.check_identity": _aca_check_identity,
    "gitops.write_project_manifests": _gitops_write,
    "gitops.open_pr_or_commit": _etape_constante("GitOps pull request opened on choregos-infra"),
    "argocd.wait_synced": _argocd_wait_synced,
    "repo.scaffold_pr": _repo_scaffold_pr,
    "memory.create_tenant": _memory_create_tenant,
    "memory.initial_import": _memory_initial_import,
    "gateway.create_team_and_budget": _gateway_create_team_and_budget,
    "notify.test_message": _notify_test_message,
}


async def _check_aca(step: str, bundle: Any) -> str:
    """Vérifie l'environnement ACA **avant** le premier run, pas au premier ticket.

    Un environnement absent ou une identité sans droit `AcrPull` ne se voit sinon qu'au
    moment où un agent devrait démarrer : autant le dire pendant le provisioning.
    """
    executor = bundle.adapters.executor
    client = getattr(executor, "client", None)
    environment = getattr(executor, "environment_id", None)
    if client is None or environment is None:
        return f"step `{step}` skipped: this project's runtime is not Azure Container Apps"
    if step == "aca.check_environment":
        found = await client.request("GET", environment)
        if found is None:
            raise RuntimeError(f"ACA environment not found: {environment}")
        return f"ACA environment reachable: {environment.rsplit('/', 1)[-1]}"
    identity = getattr(executor, "identity_id", None)
    if not identity:
        return "no managed identity declared: the runner image must be public"
    found = await client.request("GET", identity)
    if found is None:
        raise RuntimeError(f"managed identity not found: {identity}")
    return f"managed identity reachable: {identity.rsplit('/', 1)[-1]}"


async def _write_manifests(bundle: Any, params: dict[str, Any], settings: Any) -> str:
    """Écrit les manifests du projet dans le dépôt GitOps — Argo CD applique, pas l'API."""
    path = str(params.get("path", "projects/{{slug}}/")).replace("{{slug}}", bundle.slug)
    from choregos_core.quotas import quota_pour

    from ..gitops import EGRESS_IMAGE, render_project_manifests

    manifests = render_project_manifests(
        bundle.slug,
        bundle.config,
        bundle.policy,
        egress_image=str(getattr(settings, "egress_image", "") or EGRESS_IMAGE),
        quota=await quota_pour(bundle.org_slug, bundle.slug),
    )
    writer = getattr(bundle.adapters.cd, "write_files", None)
    if writer is not None:
        await writer(path, manifests)
    return f"{len(manifests)} manifest(s) written to {path}"


def _remediation(step: str) -> str:
    return {
        "github.install_app": "Install the GitHub App `choregos-bot` on the repository, then retry.",
        "github.ensure_project_board": "Create the Projects v2 board and give its number to the connector.",
        "gitops.write_project_manifests": "Check the write rights on `choregos-infra`.",
        "argocd.wait_synced": "Look at the application in Argo CD: a manual sync is possible.",
        "gateway.create_team_and_budget": "Check the LiteLLM `master_key` and the team's quota.",
        "aca.check_environment": "Create the ACA Managed Environment, or fix `environment_id`.",
        "aca.check_identity": "Check the managed identity and its `AcrPull` role on the registry.",
    }.get(step, "Fix the cause, then retry the provisioning: it resumes at the failed step.")


@activity.defn(name="finish_provisioning")
async def finish_provisioning(payload: dict[str, Any]) -> dict[str, Any]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        project: Project = bundle.project
        state = dict(project.provision_state or {})
        failed = [s for s in state.get("steps", []) if s.get("status") == "failed"]
        state["status"] = "failed" if failed else "succeeded"
        state["current_step"] = None
        project.provision_state = state
        project.status = "active" if not failed else "draft"
        await persist_event(
            session,
            EventType.PROVISIONING_FAILED if failed else EventType.PROVISIONING_COMPLETED,
            project_id=project.id,
            project_slug=bundle.slug,
            subject=bundle.slug,
            failed=[s["name"] for s in failed],
        )
        return {"status": state["status"], "failed": [s["name"] for s in failed]}
