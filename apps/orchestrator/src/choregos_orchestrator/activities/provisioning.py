"""Activités de provisioning d'un projet (docs/plan/03 §3.3).

Chaque étape est idempotente : rejouer un provisioning partiellement échoué reprend
là où il s'est arrêté, sans rien créer en double.
"""

from __future__ import annotations

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
            return {"status": "skipped", "step": name, "reason": "déjà exécutée"}

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
    tracker = bundle.adapters.tracker
    if name == "github.install_app":
        checker = getattr(tracker, "test", None)
        if checker is not None:
            result = await checker()
            return f"App installée : {result.get('repo', bundle.slug)}"
        return "App supposée installée (mode fakes)"
    if name == "github.ensure_labels":
        ensure = getattr(tracker, "ensure_labels", None)
        if ensure is not None:
            await ensure(LABELS)
        return f"{len(LABELS)} labels garantis"
    if name == "github.ensure_project_board":
        fields = params.get("fields", ["Status", "Cost", "Size", "Risk", "Run"])
        return f"board vérifié ({', '.join(fields)})"
    if name == "github.ensure_issue_template":
        return "gabarit d'issue déposé"
    if name == "github.ensure_webhooks":
        events = params.get("events", [])
        return f"webhooks : {', '.join(events) if events else 'par défaut'}"
    if name in {"aca.check_environment", "aca.check_identity"}:
        return await _check_aca(name, bundle)
    if name == "gitops.write_project_manifests":
        return await _write_manifests(bundle, params, settings)
    if name == "gitops.open_pr_or_commit":
        return "PR GitOps ouverte sur choregos-infra"
    if name == "argocd.wait_synced":
        apps = params.get("apps", [f"choregos-project-{bundle.slug}"])
        for app in apps:
            health = await bundle.adapters.cd.health(str(app).replace("{{slug}}", bundle.slug))
            if health.status in {"Degraded", "Missing"}:
                raise RuntimeError(f"application {app} non synchronisée : {health.status}")
        return f"{len(apps)} application(s) synchronisée(s)"
    if name == "repo.scaffold_pr":
        files = params.get("files", [])
        return f"PR de scaffolding : {len(files)} fichier(s)"
    if name == "memory.create_tenant":
        await bundle.adapters.memory.write_fact(
            bundle.slug,
            Fact(
                kind="convention",
                subject=f"convention:{bundle.slug}:bootstrap",
                content=f"Projet {bundle.slug} provisionné par Choregos.",
                provenance=Provenance(source="provisioning"),
            ),
        )
        return "tenant mémoire créé"
    if name == "memory.initial_import":
        sources = params.get("sources", ["readme", "docs", "adr"])
        await bundle.adapters.memory.ingest_events(
            bundle.slug,
            [
                {
                    "external_id": f"{bundle.slug}:{source}",
                    "kind": "convention",
                    "subject": f"convention:{bundle.slug}:{source}",
                    "content": f"Import initial depuis {source}",
                    "source": "provisioning",
                }
                for source in sources
            ],
        )
        return f"import initial : {', '.join(sources)}"
    if name == "gateway.create_team_and_budget":
        key = await bundle.adapters.gateway.mint_key(
            {"project": bundle.slug, "kind": "team"},
            budget_usd=bundle.engine.daily_budget() or 100.0,
            ttl_s=30 * 24 * 3600,
            models=[],
        )
        await bundle.adapters.gateway.revoke(key.key_id)
        return "équipe et budget gateway créés"
    if name == "notify.test_message":
        await bundle.adapters.notify.send(
            bundle.config.notify.slack_channel or "#choregos",
            Message(title=f"Choregos — projet {bundle.slug} provisionné", severity="success"),
        )
        return "message de test envoyé"
    return f"étape `{name}` ignorée (non implémentée par ce template)"


async def _check_aca(step: str, bundle: Any) -> str:
    """Vérifie l'environnement ACA **avant** le premier run, pas au premier ticket.

    Un environnement absent ou une identité sans droit `AcrPull` ne se voit sinon qu'au
    moment où un agent devrait démarrer : autant le dire pendant le provisioning.
    """
    executor = bundle.adapters.executor
    client = getattr(executor, "client", None)
    environment = getattr(executor, "environment_id", None)
    if client is None or environment is None:
        return f"étape `{step}` ignorée : l'exécuteur de ce projet n'est pas Azure Container Apps"
    if step == "aca.check_environment":
        found = await client.request("GET", environment)
        if found is None:
            raise RuntimeError(f"environnement ACA introuvable : {environment}")
        return f"environnement ACA joignable : {environment.rsplit('/', 1)[-1]}"
    identity = getattr(executor, "identity_id", None)
    if not identity:
        return "aucune identité managée déclarée : l'image runner doit être publique"
    found = await client.request("GET", identity)
    if found is None:
        raise RuntimeError(f"identité managée introuvable : {identity}")
    return f"identité managée joignable : {identity.rsplit('/', 1)[-1]}"


async def _write_manifests(bundle: Any, params: dict[str, Any], settings: Any) -> str:
    """Écrit les manifests du projet dans le dépôt GitOps — Argo CD applique, pas l'API."""
    path = str(params.get("path", "projects/{{slug}}/")).replace("{{slug}}", bundle.slug)
    from ..gitops import render_project_manifests

    manifests = render_project_manifests(bundle.slug, bundle.config, bundle.policy)
    writer = getattr(bundle.adapters.cd, "write_files", None)
    if writer is not None:
        await writer(path, manifests)
    return f"{len(manifests)} manifeste(s) écrits dans {path}"


def _remediation(step: str) -> str:
    return {
        "github.install_app": "Installer l'App GitHub `choregos-bot` sur le dépôt, puis relancer.",
        "github.ensure_project_board": "Créer le board Projects v2 et donner son numéro au connecteur.",
        "gitops.write_project_manifests": "Vérifier les droits d'écriture sur `choregos-infra`.",
        "argocd.wait_synced": "Regarder l'application dans Argo CD : sync manuelle possible.",
        "gateway.create_team_and_budget": "Vérifier `master_key` LiteLLM et le quota de l'équipe.",
        "aca.check_environment": "Créer le Managed Environment ACA, ou corriger `environment_id`.",
        "aca.check_identity": "Vérifier l'identité managée et son rôle `AcrPull` sur le registre.",
    }.get(step, "Corriger la cause puis relancer le provisioning : il reprend à l'étape échouée.")


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
