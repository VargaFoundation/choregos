# SPDX-License-Identifier: Apache-2.0
"""Socle des activités : accès base, adaptateurs par projet, idempotence.

Toutes les activités sont **idempotentes** par `(run_id | work_item_id, step)` : Temporal
peut les rejouer sans effet double — c'est la règle qui protège du double coût.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from choregos_adapters import AdapterSet, charger_les_greffons
from choregos_api.adaptateurs import brancher_memoire_lexicale
from choregos_api.db.models import Connector, Organization, PolicyDef, Project, WorkItem
from choregos_api.db.session import TOUT, session_scope
from choregos_api.services import default_workflow, policy_model, workflow_model
from choregos_api.services import workflow_du_ticket as _workflow_du_ticket
from choregos_api.services.definitions import ceiling_of
from choregos_api.services.github_app import pin_for_runtime
from choregos_api.services.plugin_tracker import pin_plugin_tracker
from choregos_contracts import Policy, ProjectConfig, Workflow
from choregos_core import Message, PolicyEngine
from choregos_core.memory_scope import memory_key
from choregos_core.policy_ceiling import PolicyCeiling, clamp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

# L'orchestrateur possède les mêmes tables que l'API : il branche le repli lexical pareil.
brancher_memoire_lexicale()
# … et les greffons déclarés hors de l'arbre, comme l'API.
charger_les_greffons()


@dataclass(slots=True)
class ProjectBundle:
    """Tout ce qu'une activité doit connaître d'un projet, chargé en une fois."""

    project: Project
    org_slug: str
    config: ProjectConfig
    #: Le workflow PAR DÉFAUT du projet (ADR 0031) : celui où naît un ticket qui ne dit pas le sien.
    #: Pour un ticket existant, c'est `workflow_du_ticket` qui fait foi — jamais celui-ci.
    workflow: Workflow
    policy: Policy
    adapters: AdapterSet
    #: La version de ce défaut, pour épingler un ticket à sa naissance.
    workflow_def_id: str | None = None
    #: The key the organisation's global memories live under (S40-24): its first slug, which a later
    #: rename does not move. Empty when the bundle has no organisation.
    org_memory_key: str = ""
    #: The adapter of the organisation's global memories (S40-11); built on first use by `org_memory`.
    org_memory_adapter: Any = None
    #: The organisation's policy ceiling, if it set one: its `network` section bounds the run's
    #: network (ADR 0070). `policy` above is already brought down to it.
    ceiling: PolicyCeiling | None = None

    @property
    def engine(self) -> PolicyEngine:
        return PolicyEngine(self.policy)

    @property
    def slug(self) -> str:
        return self.project.slug


@asynccontextmanager
async def db() -> AsyncIterator[AsyncSession]:
    """Une activité agit pour la plateforme : elle voit toutes les organisations.

    C'est un choix dit, pas un oubli : la RLS est fail-closed, et une session qui ne se
    nomme pas ne voit rien. L'orchestrateur reçoit des identifiants de projet (UUID) des
    workflows, pas des slugs devinés.
    """
    async with session_scope(orgs="*") as session:
        yield session


async def load_project(session: AsyncSession, project_id: str) -> ProjectBundle:
    project = await session.get(Project, project_id)
    if project is None:
        rows = (await session.execute(select(Project).where(Project.slug == project_id))).scalars().all()
        if len(rows) > 1:
            # Un slug dans deux organisations : « le premier » serait celui d'un autre locataire.
            raise ValueError(f"ambiguous project: {project_id} exists in several organisations")
        project = rows[0] if rows else None
    if project is None:
        raise ValueError(f"projet inconnu : {project_id}")
    org = await session.get(Organization, project.org_id)
    workflow_row = await default_workflow(session, project.id)
    policy_row = (
        await session.execute(
            select(PolicyDef)
            .where(PolicyDef.project_id == project.id, PolicyDef.is_active.is_(True))
            .order_by(PolicyDef.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    connectors = (
        (await session.execute(select(Connector).where(Connector.project_id == project.id))).scalars().all()
    )
    # The sealed `store:` values of the project's connectors, read here with the organisation's
    # context (the sync adapter factory cannot): `from_connectors` receives them as `resolved`.
    sealed = {c.kind: await _sealed_values(session, project.org_id, c) for c in connectors}
    # A GitHub connector builds with the installation its organisation claimed, injected here and
    # never stored (ADR 0073); on a multi-organisation instance an unclaimed owner raises.
    configs = {
        c.kind: await pin_plugin_tracker(
            session,
            project.org_id,
            c.kind,
            c.type,
            await pin_for_runtime(session, project.org_id, c.kind, c.type, dict(c.config or {})),
        )
        for c in connectors
    }
    return ProjectBundle(
        project=project,
        org_slug=org.slug if org else "",
        org_memory_key=memory_key(org) if org else "",
        config=ProjectConfig.model_validate(project.config),
        workflow=workflow_model(workflow_row),
        workflow_def_id=workflow_row.id if workflow_row is not None else None,
        # The policy that runs: the project's, brought down to its organisation's ceiling (S27-05).
        policy=clamp(policy_model(policy_row), ceiling_of(org))[0],
        ceiling=ceiling_of(org),
        adapters=AdapterSet.from_connectors(
            {
                # La mémoire `lexical` vit dans les tables de l'API : elle doit dire depuis
                # quelle organisation elle lit. L'orchestrateur est un processus de la
                # plateforme, il voit tout (`*`) — comme ses propres sessions (`db()`).
                c.kind: {
                    "type": c.type,
                    "config": {**configs[c.kind], "org": TOUT} if c.kind == "memory" else configs[c.kind],
                    # Des RÉFÉRENCES (ADR 0034) : `from_connectors` les résout en construisant.
                    "secret_refs": c.secret_refs or {},
                    "secret_ref": c.secret_ref,
                    "resolved": sealed[c.kind],
                }
                for c in connectors
            }
        ),
    )


async def _sealed_values(session: AsyncSession, org_id: str, connector: Connector) -> dict[str, str]:
    """field -> value for a connector's `store:` references. A value that cannot be read is left
    out: the adapter's build then fails loudly on that reference, for that connector only — the
    other connectors of the project stay usable."""
    from choregos_api.secret_store import resolve_store_fields
    from choregos_core.secrets import SecretIntrouvable

    try:
        return await resolve_store_fields(
            session, org_id, connector.kind, connector.type, connector.secret_refs, connector.secret_ref
        )
    except SecretIntrouvable:
        return {}


def tracker_possede_les_tickets(adapter: object) -> bool:
    """Le connecteur tient-il les tickets ailleurs ? Un connecteur muet est supposé oui.

    Un adaptateur tiers, écrit avant que la question se pose, n'a pas cet attribut : le
    supposer externe est le choix sûr, puisque c'est le cas de tous les connecteurs livrés
    sauf `internal`.
    """
    return bool(getattr(adapter, "owns_items", True))


async def cle_de_ticket_interne(session: AsyncSession, bundle: ProjectBundle) -> str:
    """Voir `choregos_api.services.cle_de_ticket_interne` : un seul endroit attribue les clés."""
    from choregos_api.services import cle_de_ticket_interne as attribuer

    return await attribuer(session, bundle.project)


async def workflow_du_ticket(session: AsyncSession, item: WorkItem) -> Workflow:
    """Le workflow où le ticket est né, à sa version (ADR 0031) — à défaut, celui du projet."""
    return workflow_model(await _workflow_du_ticket(session, item))


async def load_work_item(session: AsyncSession, work_item_id: str) -> WorkItem:
    item = await session.get(WorkItem, work_item_id)
    if item is None:
        item = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == work_item_id).limit(1))
        ).scalar_one_or_none()
    if item is None:
        raise ValueError(f"unknown work item: {work_item_id}")
    return item


_ADAPTERS_OVERRIDE: AdapterSet | None = None


def set_adapters_override(adapters: AdapterSet | None) -> None:
    """Injection utilisée par les tests et par `make demo` : un seul jeu d'adaptateurs partagé."""
    global _ADAPTERS_OVERRIDE
    _ADAPTERS_OVERRIDE = adapters


def adapters_override() -> AdapterSet | None:
    return _ADAPTERS_OVERRIDE


async def project_bundle(session: AsyncSession, project_id: str) -> ProjectBundle:
    bundle = await load_project(session, project_id)
    override = adapters_override()
    if override is not None:
        bundle.adapters = override
    return bundle


async def org_memory(session: AsyncSession, bundle: ProjectBundle) -> Any:
    """The adapter that serves the organisation's global memories (S40-11, ADR 0075), built once per bundle.

    The organisation's `memory` connector when it declared one, else the deployment default — the
    adapter a project without a memory connector gets. Under fakes it is the bundle's own memory
    fake, which keys facts by namespace, so a global memory never mixes with a project's.
    """
    if bundle.org_memory_adapter is not None:
        return bundle.org_memory_adapter
    from choregos_adapters import build, configuration_resolue, fakes_enabled
    from choregos_adapters.registry import TYPES_PAR_DEFAUT
    from choregos_api.db.models import OrgConnector

    if fakes_enabled() or adapters_override() is not None:
        bundle.org_memory_adapter = bundle.adapters.memory
        return bundle.org_memory_adapter
    row = (
        (
            await session.execute(
                select(OrgConnector)
                .where(OrgConnector.org_id == bundle.project.org_id, OrgConnector.kind == "memory")
                .order_by(OrgConnector.name)
            )
        )
        .scalars()
        .first()
    )
    type_name = row.type if row is not None else TYPES_PAR_DEFAUT["memory"]
    config = dict(row.config or {}) if row is not None else {}
    # As the project's own memory: a platform process, seen from every organisation (`*`).
    config = {**config, "org": TOUT}
    sealed = await _sealed_org_values(session, bundle.project.org_id, row) if row is not None else {}
    resolved = configuration_resolue(
        "memory", type_name, config, row.secret_refs if row is not None else None, None, preresolved=sealed
    )
    bundle.org_memory_adapter = build("memory", type_name, resolved)
    return bundle.org_memory_adapter


async def _sealed_org_values(session: AsyncSession, org_id: str, connector: Any) -> dict[str, str]:
    from choregos_api.secret_store import resolve_store_fields
    from choregos_core.secrets import SecretIntrouvable

    try:
        return await resolve_store_fields(
            session, org_id, connector.kind, connector.type, connector.secret_refs, None
        )
    except SecretIntrouvable:
        return {}


def as_dict(value: Any) -> dict[str, Any]:
    """Sérialise un modèle pydantic ou un dict pour traverser une frontière d'activité."""
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json", by_alias=True))
    return dict(value or {})


async def send_courtesy(notify: Any, channel: str, message: Message) -> bool:
    """Send a notification that is a courtesy, not the act: never raise, say whether it went out.

    A deployment without a notifier (the dev tenant has no Slack) makes `notify.send` raise
    the Slack "no `bot_token` nor `webhook_url`" error. Raised from an activity, that killed what
    sent it: the human request (2026-09-27), the train (S22-26), then the 24 h reminder of a
    pending human request, which failed the WorkItem interpreter (#497). Every notification an
    activity sends goes through here, and the silence is logged and returned instead."""
    try:
        await notify.send(channel, message)
    # Deliberately broad: no notifier failure may kill the work that notifies.
    except Exception as exc:
        activity.logger.warning("notification NOT sent on %s (%s): %s", channel, message.title, exc)
        return False
    return True


def without_secrets(stage_input: dict[str, Any] | None, *, keep_gateway_key: bool) -> dict[str, Any]:
    """A `StageInput` payload with no run token, and with no gateway key unless asked (#519).

    Neither needs to be stored: `start_run` mints both for the run's Secret, and a resumed run gets
    fresh ones through the same Secret (#523). A row written before #523 may still hold a key: it
    is scrubbed when the run starts or ends. A Temporal payload never carries either.
    """
    from choregos_core.redaction import REDACTED

    cleaned = dict(stage_input or {})
    if "callbacks" in cleaned:
        cleaned["callbacks"] = {**dict(cleaned.get("callbacks") or {}), "run_token": REDACTED}
    if not keep_gateway_key and cleaned.get("gateway_key") is not None:
        cleaned["gateway_key"] = None
    return cleaned
