# SPDX-License-Identifier: Apache-2.0
"""Provisioning activities of a project (docs/plan/03 §3.3).

Every step is idempotent: replaying a partially failed provisioning resumes where it stopped,
creating nothing twice.
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
from .base import db, notification_target, project_bundle


def templates_dir() -> Path:
    """Where the project templates live: `CHOREGOS_TEMPLATES_DIR`, else the repository's `templates/`.

    `parents[5]` assumed the layout of the source repository; in the image it is `/app`, and
    `templates/` was not even copied there. The setting says where to look, and an absence shows
    on the first read, with the path searched.
    """
    import os

    configured = os.environ.get("CHOREGOS_TEMPLATES_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[5] / "templates"


TEMPLATES_DIR = templates_dir()

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
    """Read the template manifest and return the ordered list of steps."""
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
    """Run a provisioning step and update the state visible in the front end (SSE)."""
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
        except Exception as exc:  # a failing step is repairable, not fatal
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
    """Dispatch the steps. Any unknown step is skipped explicitly, never guessed."""
    handler = _STEPS.get(name)
    if handler is None:
        return _unknown_step(name)
    return str(await handler(params, bundle, settings))


async def _github_install_app(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    checker = getattr(bundle.adapters.tracker, "test", None)
    if checker is not None:
        result = await checker()
        return f"App installed: {result.get('repo', bundle.slug)}"
    return "App assumed installed (fakes mode)"


#: The colour of a label that a template names without colouring it.
DEFAULT_COLOUR = "ededed"


def step_labels(params: dict[str, Any]) -> dict[str, str]:
    """The platform's labels, then those the template adds (`labels:`, a list of names or a
    name -> colour dictionary): a template's routing reads labels the repository does not have
    yet (`bug`, `adr`, `complex`...), and a work item labelled with an absent label is not born in
    the right workflow (S21-22). A platform colour is never replaced."""
    added = params.get("labels") or {}
    if isinstance(added, list):
        added = dict.fromkeys((str(name) for name in added), DEFAULT_COLOUR)
    return {**{str(name): str(colour).lstrip("#") for name, colour in dict(added).items()}, **LABELS}


async def _github_ensure_labels(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    labels = step_labels(params)
    ensure = getattr(bundle.adapters.tracker, "ensure_labels", None)
    if ensure is not None:
        await ensure(labels)
    return f"{len(labels)} labels ensured"


#: The board fields when the template names none.
BOARD_FIELDS = ["Status", "Cost (€)", "Size", "Risk", "Run"]


async def _github_ensure_project_board(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    """The board fields exist under their English name; a field that a Choregos older than S22-21
    created in French (`Taille`) is renamed, not doubled: its values stay on the cards."""
    fields = [str(name) for name in params.get("fields") or BOARD_FIELDS]
    ensure = getattr(bundle.adapters.tracker, "ensure_project_fields", None)
    if ensure is None:
        return f"board checked ({', '.join(fields)})"
    outcome = await ensure(fields)
    if not any(outcome.values()):
        return "no Projects v2 board configured: give its number to the connector"
    parts = [
        f"{len(outcome.get('kept', []))} kept",
        f"{len(outcome.get('created', []))} created",
        f"{len(outcome.get('renamed', []))} renamed",
    ]
    renamed = outcome.get("renamed", [])
    return f"board fields: {', '.join(parts)}" + (f" ({'; '.join(renamed)})" if renamed else "")


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


def project_team_id(project_id: str) -> str:
    """The gateway team of a project: deterministic, so every run's key joins the same one."""
    return f"choregos-{project_id}"


async def _gateway_create_team_and_budget(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    """The project's gateway team, with its day budget (S27-10, ADR 0047). Before, this step minted a
    key and revoked it at once: it checked the master key, and created no team."""
    daily = bundle.engine.daily_budget()
    if daily is None:
        return "no daily project budget in the policy: no gateway team"
    team = await bundle.adapters.gateway.ensure_team(project_team_id(bundle.project.id), daily)
    return f"gateway team `{team}` created, budget {daily:.2f} $ per day"


async def _notify_test_message(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    # The one notification that is NOT sent as a courtesy (`base.send_courtesy`, #497): this step
    # exists to prove the notifier works, so a failure is its result. It fails the step, which
    # `run_provision_step` records as repairable with its remediation.
    await bundle.adapters.notify.send(
        notification_target(bundle),
        Message(title=f"Choregos — project {bundle.slug} provisioned", severity="success"),
    )
    return "test message sent"


def _constant_step(text: str) -> Step:
    async def _step(params: dict[str, Any], bundle: Any, settings: Any) -> str:
        return text

    return _step


def _unknown_step(name: str) -> str:
    return f"step `{name}` skipped (not implemented for this template)"


async def _gitops_write(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    return await _write_manifests(bundle, params, settings)


async def _repo_scaffold_pr(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    return f"scaffolding pull request: {len(params.get('files', []))} file(s)"


async def _aca_check_environment(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    return await _check_aca("aca.check_environment", bundle)


async def _aca_check_identity(params: dict[str, Any], bundle: Any, settings: Any) -> str:
    return await _check_aca("aca.check_identity", bundle)


Step = Callable[[dict[str, Any], Any, Any], Awaitable[str]]

#: A step = a function; what a template names and is not here is skipped, and says so.
_STEPS: dict[str, Step] = {
    "github.install_app": _github_install_app,
    "github.ensure_labels": _github_ensure_labels,
    "github.ensure_project_board": _github_ensure_project_board,
    "github.ensure_issue_template": _constant_step("issue template added"),
    "github.ensure_webhooks": _github_ensure_webhooks,
    "aca.check_environment": _aca_check_environment,
    "aca.check_identity": _aca_check_identity,
    "gitops.write_project_manifests": _gitops_write,
    "gitops.open_pr_or_commit": _constant_step("GitOps pull request opened on choregos-infra"),
    "argocd.wait_synced": _argocd_wait_synced,
    "repo.scaffold_pr": _repo_scaffold_pr,
    "memory.create_tenant": _memory_create_tenant,
    "memory.initial_import": _memory_initial_import,
    "gateway.create_team_and_budget": _gateway_create_team_and_budget,
    "notify.test_message": _notify_test_message,
}


async def _check_aca(step: str, bundle: Any) -> str:
    """Check the ACA environment **before** the first run, not at the first work item.

    A missing environment or an identity without the `AcrPull` right shows otherwise only when an
    agent should start: better to say so during the provisioning.
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
    """Write the project manifests into the GitOps repository: Argo CD applies, not the API."""
    path = str(params.get("path", "projects/{{slug}}/")).replace("{{slug}}", bundle.slug)
    from choregos_core.quotas import quota_for

    from ..gitops import EGRESS_IMAGE, render_project_manifests
    from ..org_egress import org_runner_hosts

    org_id = getattr(getattr(bundle, "project", None), "org_id", None)
    org_hosts: list[str] = []
    if org_id:
        async with db() as session:
            org_hosts = await org_runner_hosts(session, org_id)
    manifests = render_project_manifests(
        bundle.slug,
        bundle.config,
        bundle.policy,
        egress_image=str(getattr(settings, "egress_image", "") or EGRESS_IMAGE),
        quota=await quota_for(bundle.org_slug, bundle.slug),
        org_allow_domains=org_hosts,
    )
    writer = getattr(bundle.adapters.cd, "write_files", None)
    if writer is not None:
        await writer(path, manifests)
    return f"{len(manifests)} manifest(s) written to {path}"


def _remediation(step: str) -> str:
    return {
        "github.install_app": (
            "Install the Choregos GitHub App from the console (Settings, Connections, Install on GitHub), "
            "then retry. The installation is discovered: nobody types an installation id."
        ),
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
        slug = bundle.slug
        memory_enabled = bundle.engine.memory_enabled()
    if not failed:
        # After the commit: the loop's first pass reads the project. Best effort, the worker's next
        # start catches a missing loop up; the provisioning itself succeeded.
        from ..train_client import start_reconciliation_loop

        try:
            await start_reconciliation_loop(slug)
        except Exception as exc:  # the project is active; the worker start catches up
            activity.logger.warning("tracker catch-up not started for %s: %s", slug, exc)
        # The memory is fed from activation on (S51-02), unless the project's policy disables it.
        if memory_enabled:
            from ..train_client import start_memory_loop

            try:
                await start_memory_loop(slug)
            except Exception as exc:  # the project is active; the worker start catches up
                activity.logger.warning("memory loop not started for %s: %s", slug, exc)
    return {"status": state["status"], "failed": [s["name"] for s in failed]}


repertoire_templates = templates_dir  # lang: fr-ok alias:internal
etiquettes_du_pas = step_labels  # lang: fr-ok alias:internal
