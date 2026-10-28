# SPDX-License-Identifier: Apache-2.0
"""What a project still lacks to run, computed from stored state (S40-06).

`load_facts` reads everything once; each `item_*` function is pure over the resulting `Facts`, so a
unit test needs no database. Nothing here calls the network except one probe: when an active workflow
declares a file output, the
object store is asked once (a `HEAD` on its bucket, 5 s at most, cached for a minute) because a store
that cannot be written to is otherwise found by the first run that writes a file (S38-13c). A connector's
state is the one its last test left, which keeps the read fast and lets the CLI and the MCP door show the
same list as the console.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml
from choregos_adapters.storage import ObjectStoreError
from choregos_contracts import ARTIFACT_TYPES, ProjectConfig
from choregos_core import ModelResolutionError, backend_enabled, checksum, parse_workflow
from choregos_core import ValidationError as WorkflowInvalid
from choregos_core.models import ModelResolver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    BackendRegistryRow,
    Connector,
    Membership,
    ModelProfileRow,
    Project,
    WorkflowDef,
    WorkItem,
)
from ..errors import ApiError
from ..object_store import object_store
from ..schemas.readiness import ProjectReadiness, ReadinessFix, ReadinessItem, ReadinessSummary
from . import github_app
from .definitions import active_policy, policy_model, workflow_model
from .gabarits import manifeste_du_gabarit, shipped_workflows
from .human_groups import groups_known_in, unknown_group_warnings
from .projets import declares_a_release_train
from .workflow_catalogue import parse_origin

#: A test older than this is worth running again.
STALE_TEST = timedelta(days=7)
#: Connector types that stand in for the real thing.
STAND_INS = {"demo", "fake"}
#: Capabilities the deployment provides when a project does not configure one: the executor
#: (`CHOREGOS_EXECUTOR_KIND`), the gateway, the memory service, and the internal tracker.
PROVIDED_BY_THE_DEPLOYMENT = frozenset({"runtime", "gateway", "memory", "tracker"})
#: Secret fields that never block a connector: a webhook secret only matters once GitHub can reach us.
OPTIONAL_SECRETS = {"webhook_secret"}
GITHUB_KINDS = {"tracker": "github-issues", "scm": "github"}
#: Capabilities that only make sense for a project that delivers code from a repository.
NEED_A_REPOSITORY = frozenset({"scm", "ci", "cd"})

CAPABILITY_TITLES = {
    "tracker": "Tracker",
    "scm": "Repository (SCM)",
    "ci": "Continuous integration",
    "cd": "Continuous delivery",
    "runtime": "Agent runtime",
    "gateway": "Model gateway",
    "memory": "Memory",
    "notify": "Notifications",
}


@dataclass(frozen=True)
class ConnectorFact:
    kind: str
    type: str
    status: str
    last_check_at: datetime | None
    last_error: str | None
    config: dict[str, Any]
    #: secret fields the type declares and the connector needs
    secrets_expected: tuple[str, ...] = ()
    #: those that are set (a sealed value that exists, or a reference)
    secrets_set: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ObjectStoreFact:
    """What the probe of the object store found: `error` is None when the store answered."""

    error: str | None = None


@dataclass(frozen=True)
class RuntimeFact:
    """The project's policy asks for a RuntimeClass and the deployment's warm pool is known.

    `refusal` is the executor's own message (`runtime_class_refusal`), None when the pool runs it."""

    asked: str
    refusal: str | None = None


@dataclass(frozen=True)
class TemplateWorkflowFact:
    """An active workflow the project received from its template, against what the template ships now.

    Both checksums are computed now, by the same parser, from the stored YAML and from the template's
    file: a parser that learnt a new default does not make every stored copy look different."""

    name: str
    #: the project's active version of it
    version: int
    stored: str
    shipped: str
    #: where the template takes it from (`workflows/campaign.yaml`, `template:default-simple@1`)
    ref: str


@dataclass(frozen=True)
class Facts:
    slug: str
    status: str
    provision_failure: str | None
    config: ProjectConfig
    #: names of the active workflows, and the default one
    workflows: tuple[str, ...]
    default_workflow: str | None
    #: (capability, reasons) as `exigences` derives them
    requirements: tuple[tuple[str, tuple[str, ...]], ...]
    connectors: dict[str, ConnectorFact]
    #: capabilities the deployment provides on its own
    provided: frozenset[str]
    #: does the deployment have a GitHub App; (kind, owner, claimed) of each GitHub connector
    github_app: bool = False
    github_owners: tuple[tuple[str, str, bool], ...] = ()
    #: the backends a project may use here (deployment list and the platform catalogue)
    usable_backends: frozenset[str] = frozenset({"claude-code"})
    #: profiles named by the active workflows that no project or platform profile defines
    profiles_unknown: tuple[str, ...] = ()
    #: profiles that resolve but have no eval for the default backend
    profiles_unvalidated: tuple[str, ...] = ()
    profiles_used: int = 0
    ticket_budget_set: bool = False
    daily_budget_set: bool = False
    has_owner: bool = False
    has_release_captain: bool = False
    unknown_groups: tuple[str, ...] = ()
    has_release_train: bool = False
    #: findings or memory reviews announce themselves to the notification channel
    announces: bool = False
    work_items: int = 0
    #: names of the outputs an active workflow declares with a file type (kept in the object store)
    file_outputs: tuple[str, ...] = ()
    #: the probe of the object store; None when no workflow declares a file output (nothing asked)
    object_store: ObjectStoreFact | None = None
    #: the policy's RuntimeClass against the warm pool's; None when nothing is asked, the pool is not
    #: the executor in use, or the deployment did not say which RuntimeClass it runs
    runtime: RuntimeFact | None = None
    #: the template the project was made from, and its workflows still marked `source: template`;
    #: empty when there is no template or it cannot be read any more (nothing is judged then)
    template: str | None = None
    template_workflows: tuple[TemplateWorkflowFact, ...] = ()
    #: the active workflows adopted from the catalogue (ADR 0108): (name, origin, version it offers now,
    #: None when the entry is gone)
    adopted: tuple[tuple[str, str, int | None], ...] = ()
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


def _fix(slug: str, anchor: str, by: str = "project_owner", *, page: str = "settings") -> ReadinessFix:
    return ReadinessFix(href=f"/p/{slug}/{page}" + (f"#{anchor}" if anchor else ""), by=by)


def _item(id_: str, section: str, title: str, state: str, detail: str, fix: ReadinessFix) -> ReadinessItem:
    return ReadinessItem(id=id_, section=section, title=title, state=state, detail=detail, fix=fix)


# ───────────────────────────── the items: pure functions over Facts ─────────────────────────────


def item_provisioned(f: Facts) -> list[ReadinessItem]:
    fix = _fix(f.slug, "general")
    if f.status == "active":
        return [_item("provisioned", "general", "Project provisioned", "done", "provisioning ran.", fix)]
    if f.status == "provisioning":
        return [
            _item("provisioned", "general", "Project provisioned", "todo", "provisioning is running.", fix)
        ]
    why = f" Provisioning failed at: {f.provision_failure}." if f.provision_failure else ""
    gesture = "resume it" if f.status == "suspended" else "activate it"
    return [
        _item(
            "provisioned",
            "general",
            "Project provisioned",
            "todo",
            f"the project is {f.status}: nothing runs until it is active.{why} "
            f"Settings › General: {gesture}.",
            fix,
        )
    ]


def item_workflow(f: Facts) -> list[ReadinessItem]:
    title = "A workflow published"
    fix = ReadinessFix(href=f"/p/{f.slug}/workflows", by="project_owner")
    if not f.workflows:
        return [
            _item(
                "workflow",
                "general",
                title,
                "todo",
                "a request has nowhere to go until a workflow exists.",
                fix,
            )
        ]
    if f.default_workflow is None or f.default_workflow not in f.workflows:
        return [
            _item(
                "workflow",
                "general",
                title,
                "check",
                "no active workflow is the default: a request that names none has nowhere to go.",
                fix,
            )
        ]
    return [
        _item(
            "workflow",
            "general",
            title,
            "done",
            f"{len(f.workflows)} active, `{f.default_workflow}` by default.",
            fix,
        )
    ]


def _connector_item(f: Facts, capability: str, reasons: tuple[str, ...]) -> ReadinessItem:
    title = f"{CAPABILITY_TITLES.get(capability, capability.title())} connected"
    why = reasons[0] if reasons else "a workflow needs it"
    id_ = f"connector-{capability}"
    connector = f.connectors.get(capability)
    platform = capability in f.provided
    fix = _fix(f.slug, "connections", "platform_operator" if platform else "project_owner")
    if connector is None:
        if platform:
            return _item(id_, "connections", title, "done", "provided by the platform.", fix)
        if capability in NEED_A_REPOSITORY and not _has_repository(f):
            # The project has no repository: connecting one is a choice, not the only way out.
            return _item(
                id_,
                "connections",
                title,
                "todo",
                f"{why}; none is configured. A project without a repository makes default a workflow "
                "that does not deliver code, such as `default-request`.",
                _workflow_without_repository(f),
            )
        return _item(id_, "connections", title, "todo", f"{why}; none is configured.", fix)
    if connector.type in STAND_INS:
        return _item(
            id_, "connections", title, "check", f"`{connector.type}` is a stand-in, not the real thing.", fix
        )
    if connector.status == "ok":
        return _item(id_, "connections", title, "done", f"`{connector.type}`, last test passed.", fix)
    if connector.status == "unknown":
        return _item(id_, "connections", title, "check", f"`{connector.type}` was never tested.", fix)
    error = f": {connector.last_error}" if connector.last_error else ""
    return _item(id_, "connections", title, "todo", f"`{connector.type}` failed its last test{error}.", fix)


def item_connectors(f: Facts) -> list[ReadinessItem]:
    return [_connector_item(f, capability, reasons) for capability, reasons in f.requirements]


def item_connector_secrets(f: Facts) -> list[ReadinessItem]:
    items: list[ReadinessItem] = []
    for kind, connector in sorted(f.connectors.items()):
        missing = [s for s in connector.secrets_expected if s not in connector.secrets_set]
        if not connector.secrets_expected:
            continue
        title = f"{CAPABILITY_TITLES.get(kind, kind.title())} secrets set"
        fix = _fix(f.slug, "connections")
        id_ = f"connector-{kind}-secrets"
        if missing:
            items.append(_item(id_, "connections", title, "todo", f"not set: {', '.join(missing)}.", fix))
        else:
            items.append(_item(id_, "connections", title, "done", "every secret field is set.", fix))
    return items


def item_github_app(f: Facts) -> list[ReadinessItem]:
    items: list[ReadinessItem] = []
    for kind, owner, claimed in f.github_owners:
        id_ = f"github-app-{kind}"
        title = "GitHub App installed on the repository's owner"
        fix = _fix(f.slug, "connections")
        if not f.github_app:
            items.append(
                _item(id_, "connections", title, "not_needed", "this deployment has no GitHub App.", fix)
            )
        elif claimed:
            items.append(
                _item(
                    id_,
                    "connections",
                    title,
                    "done",
                    f"installed on `{owner}`, claimed by this organisation.",
                    fix,
                )
            )
        else:
            items.append(
                _item(
                    id_,
                    "connections",
                    title,
                    "todo",
                    f"the Choregos GitHub App is not installed on `{owner}` for this organisation.",
                    fix,
                )
            )
    return items


def item_stale_tests(f: Facts) -> list[ReadinessItem]:
    items: list[ReadinessItem] = []
    for kind, connector in sorted(f.connectors.items()):
        if connector.status != "ok" or connector.last_check_at is None:
            continue
        checked = connector.last_check_at
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=UTC)
        if f.now - checked > STALE_TEST:
            days = (f.now - checked).days
            items.append(
                _item(
                    f"connector-{kind}-stale",
                    "connections",
                    f"{CAPABILITY_TITLES.get(kind, kind.title())} tested recently",
                    "check",
                    f"the last test passed {days} days ago: test it again.",
                    _fix(f.slug, "connections"),
                )
            )
    return items


def _has_repository(f: Facts) -> bool:
    return bool(f.config.repo and f.config.repo.url)


def _workflow_without_repository(f: Facts) -> ReadinessFix:
    """The way out for a project with no repository (ADR 0107 §4): publish `default-request` and make it
    the default in one gesture; once it is published, the Workflows page, where it is made the default."""
    if "default-request" in f.workflows:
        return ReadinessFix(href=f"/p/{f.slug}/workflows", by="project_owner")
    return ReadinessFix(
        href=f"/p/{f.slug}/workflows/new?template=default-request&default=1", by="project_owner"
    )


def item_repository(f: Facts) -> list[ReadinessItem]:
    reasons = dict(f.requirements).get("scm")
    if reasons is None:
        return []
    fix = _fix(f.slug, "general")
    repo = f.config.repo
    if not _has_repository(f):
        # Asked by a workflow, not by the project: one without a repository changes its workflow.
        return [
            _item(
                "repository",
                "general",
                "Repository set",
                "todo",
                f"the project names no repository URL ({reasons[0] if reasons else 'a workflow needs one'}): "
                "set one, or make default a workflow that does not deliver code, such as `default-request`.",
                _workflow_without_repository(f),
            )
        ]
    assert repo is not None
    items = [_item("repository", "general", "Repository set", "done", f"`{repo.url}`.", fix)]
    measured = bool(repo.test_command) or bool(f.config.dod.facts)
    items.append(
        _item(
            "repository-checks",
            "general",
            "Tests measured by the runner",
            "done",
            "a test command is set.",
            fix,
        )
        if measured
        else _item(
            "repository-checks",
            "general",
            "Tests measured by the runner",
            "check",
            "no test command: the runner measures no tests, lint or types, only what the agent says.",
            fix,
        )
    )
    return items


def item_backend(f: Facts) -> list[ReadinessItem]:
    backend = f.config.agent.default_backend
    fix = _fix(f.slug, "agents")
    usable = ", ".join(sorted(f.usable_backends)) or "none"
    if backend in f.usable_backends:
        return [_item("backend", "agents", "Agent backend usable", "done", f"`{backend}`.", fix)]
    return [
        _item(
            "backend",
            "agents",
            "Agent backend usable",
            "todo",
            f"`{backend}` is not enabled on this deployment (usable: {usable}).",
            ReadinessFix(href=fix.href, by="project_owner"),
        )
    ]


def item_models(f: Facts) -> list[ReadinessItem]:
    title = "Model profiles resolve"
    fix = _fix(f.slug, "agents")
    if f.profiles_used == 0:
        return [
            _item("models", "agents", title, "not_needed", "no active workflow names a model profile.", fix)
        ]
    if f.profiles_unknown:
        names = ", ".join(f"`{n}`" for n in f.profiles_unknown)
        return [
            _item("models", "agents", title, "todo", f"no project or platform profile defines {names}.", fix)
        ]
    if f.profiles_unvalidated and not f.config.models.allow_unvalidated:
        names = ", ".join(f"`{n}`" for n in f.profiles_unvalidated)
        return [
            _item(
                "models",
                "agents",
                title,
                "check",
                f"{names} has no eval for `{f.config.agent.default_backend}`: the pairing is unproven.",
                fix,
            )
        ]
    return [_item("models", "agents", title, "done", f"{f.profiles_used} profile(s) resolve.", fix)]


def item_budgets(f: Facts) -> list[ReadinessItem]:
    fix = _fix(f.slug, "guardrails")
    title = "Spending capped"
    if f.ticket_budget_set and f.daily_budget_set:
        return [_item("budgets", "guardrails", title, "done", "per-ticket and daily budgets are set.", fix)]
    missing = [
        label
        for label, ok in (
            ("`budgets.ticket_usd`", f.ticket_budget_set),
            ("`budgets.daily_project_usd`", f.daily_budget_set),
        )
        if not ok
    ]
    return [
        _item("budgets", "guardrails", title, "check", f"not set in the policy: {', '.join(missing)}.", fix)
    ]


def item_people(f: Facts) -> list[ReadinessItem]:
    members = f"/p/{f.slug}/members"
    items = [
        _item(
            "people-owner",
            "people",
            "A project owner",
            "done" if f.has_owner else "todo",
            "someone owns the project." if f.has_owner else "nobody holds the project_owner role.",
            ReadinessFix(href=members, by="org_admin"),
        )
    ]
    if f.has_release_train:
        items.append(
            _item(
                "people-release-captain",
                "people",
                "A release captain",
                "done" if f.has_release_captain else "todo",
                "a release captain is a member."
                if f.has_release_captain
                else "the workflows run a release train and nobody holds the release_captain role.",
                ReadinessFix(href=members, by="project_owner"),
            )
        )
    for group in f.unknown_groups:
        items.append(
            _item(
                f"people-group-{group}",
                "people",
                f"Group `{group}` exists",
                "check",
                f"nobody in the organisation has carried the identity-provider group `{group}` at sign-in: "
                "a step addressed to it waits for an administrator.",
                ReadinessFix(href=members, by="external"),
            )
        )
    return items


def item_notifications(f: Facts) -> list[ReadinessItem]:
    title = "Notifications reach someone"
    fix = _fix(f.slug, "notifications")
    if not f.has_release_train and not f.announces:
        return [
            _item("notifications", "notifications", title, "not_needed", "nothing announces itself.", fix)
        ]
    strong = f.has_release_train
    notify = f.connectors.get("notify")
    channel = f.config.notify.slack_channel
    state_if_missing = "todo" if strong else "check"
    what = "the release train announces and asks" if strong else "findings and memory reviews announce"
    if notify is None:
        return [
            _item(
                "notifications",
                "notifications",
                title,
                state_if_missing,
                f"{what}, but the project has no notify connector: the default channel is unconfigured.",
                fix,
            )
        ]
    if notify.type in STAND_INS:
        return [
            _item("notifications", "notifications", title, "check", f"`{notify.type}` is a stand-in.", fix)
        ]
    if notify.status not in {"ok", "unknown"}:
        return [
            _item(
                "notifications",
                "notifications",
                title,
                state_if_missing,
                "the notify connector failed its last test.",
                fix,
            )
        ]
    if not channel and not f.config.notify.emails:
        return [
            _item(
                "notifications",
                "notifications",
                title,
                state_if_missing,
                f"{what}, but no Slack channel or e-mail is set.",
                fix,
            )
        ]
    if notify.status == "unknown":
        return [
            _item(
                "notifications",
                "notifications",
                title,
                "check",
                "the notify connector was never tested.",
                fix,
            )
        ]
    return [
        _item("notifications", "notifications", title, "done", "a connector and a destination are set.", fix)
    ]


def item_tools(f: Facts) -> list[ReadinessItem]:
    tools = f.config.tools
    fix = _fix(f.slug, "tools")
    detail = (
        "no catalogue tool is opened to this project."
        if not tools
        else "the whole catalogue is opened to this project."
        if "*" in tools
        else f"{len(tools)} catalogue tool(s) opened."
    )
    return [_item("tools", "tools", "Catalogue tools", "done", detail, fix)]


def item_first_work_item(f: Facts) -> list[ReadinessItem]:
    done = f.work_items > 0
    return [
        _item(
            "first-item",
            "general",
            "A first request",
            "done" if done else "check",
            f"{f.work_items} request(s) filed." if done else "nothing has been filed yet.",
            ReadinessFix(href=f"/p/{f.slug}/items", by="project_owner"),
        )
    ]


def item_object_store(f: Facts) -> list[ReadinessItem]:
    if not f.file_outputs:
        return []
    title = "Object store reachable"
    fix = ReadinessFix(href=None, by="platform_operator")
    shown = ", ".join(f"`{n}`" for n in f.file_outputs[:3]) + (", ..." if len(f.file_outputs) > 3 else "")
    if f.object_store is None or f.object_store.error is None:
        return [_item("object-store", "connections", title, "done", f"it answers for {shown}.", fix)]
    return [
        _item(
            "object-store",
            "connections",
            title,
            "todo",
            f"a workflow writes files ({shown}) and the object store fails its probe: "
            f"{f.object_store.error}. Set `global.objectStore` (endpoint, secretRef).",
            fix,
        )
    ]


def item_runtime_class(f: Facts) -> list[ReadinessItem]:
    if f.runtime is None:
        return []
    title = "Warm pool runs the policy's runtime"
    fix = ReadinessFix(href=None, by="platform_operator")
    if f.runtime.refusal is None:
        return [
            _item("runtime-class", "connections", title, "done", f"the pool runs `{f.runtime.asked}`.", fix)
        ]
    return [
        _item(
            "runtime-class",
            "connections",
            title,
            "todo",
            f"the policy asks for `sandbox.runtime: {f.runtime.asked}` and every run would be refused: "
            f"{f.runtime.refusal}.",
            fix,
        )
    ]


def item_workflow_template(f: Facts) -> list[ReadinessItem]:
    """A project keeps the copy of its template's workflows made the day it was created (#817)."""
    if f.template is None or not f.template_workflows:
        return []
    title = "Workflows as the template ships them"
    fix = ReadinessFix(href=f"/p/{f.slug}/workflows", by="project_owner")
    behind = [w for w in f.template_workflows if w.stored != w.shipped]
    if not behind:
        shown = ", ".join(f"`{w.name}`" for w in f.template_workflows)
        return [
            _item(
                "workflow-template",
                "general",
                title,
                "done",
                f"{shown} as `{f.template}` ships them on this platform.",
                fix,
            )
        ]
    differs = "; ".join(
        f"`{w.name}` v{w.version} ({w.stored[7:19]}) is not `{w.ref}` ({w.shipped[7:19]})" for w in behind
    )
    return [
        _item(
            "workflow-template",
            "general",
            title,
            "check",
            f"`{f.template}` ships a different version of what this project received: {differs}. "
            "A fix to the template does not reach a project on its own: publish the template's file "
            "as the next version (`choregos workflow push`, or `PUT /projects/{id}/workflows/{name}` "
            "with `source: template`); a work item in progress stays on its version.",
            fix,
        )
    ]


def item_workflow_catalogue(f: Facts) -> list[ReadinessItem]:
    """An adopted workflow whose catalogue entry has a newer version (ADR 0108 §5): offered, never applied."""
    if not f.adopted:
        return []
    title = "Adopted workflows up to date"
    fix = ReadinessFix(href=f"/p/{f.slug}/workflows/catalogue", by="project_owner")
    behind: list[str] = []
    for name, origin, offered in f.adopted:
        parts = parse_origin(origin)
        if parts is None:
            continue
        if offered is None:
            behind.append(f"`{name}` came from `{origin}`, which the catalogue no longer offers")
        elif offered > parts[2]:
            behind.append(f"`{name}` is `{origin}`; version {offered} is offered")
    if not behind:
        shown = ", ".join(f"`{name}`" for name, _, _ in f.adopted)
        return [
            _item(
                "workflow-catalogue", "general", title, "done", f"{shown} as the catalogue offers them.", fix
            )
        ]
    return [
        _item(
            "workflow-catalogue",
            "general",
            title,
            "check",
            "; ".join(behind) + ". Adopt it again to publish the next version; a work item in progress "
            "stays on its version.",
            fix,
        )
    ]


async def adopted_facts(
    session: AsyncSession, org_id: str, rows: list[Any]
) -> tuple[tuple[str, str, int | None], ...]:
    adopted = [row for row in rows if row.source == "catalogue" and row.origin]
    if not adopted:
        return ()
    from .workflow_catalogue import offered

    current = {(o.source, o.name): o.version for o in await offered(session, org_id)}
    out: list[tuple[str, str, int | None]] = []
    for row in adopted:
        parts = parse_origin(row.origin)
        out.append((row.name, row.origin, current.get((parts[0], parts[1])) if parts else None))
    return tuple(sorted(out))


ITEMS: tuple[Callable[[Facts], list[ReadinessItem]], ...] = (
    item_provisioned,
    item_workflow,
    item_connectors,
    item_connector_secrets,
    item_github_app,
    item_stale_tests,
    item_repository,
    item_backend,
    item_models,
    item_budgets,
    item_people,
    item_notifications,
    item_object_store,
    item_runtime_class,
    item_workflow_template,
    item_workflow_catalogue,
    item_tools,
    item_first_work_item,
)


def evaluate(facts: Facts) -> ProjectReadiness:
    items = [item for function in ITEMS for item in function(facts)]
    summary = ReadinessSummary(
        done=sum(i.state == "done" for i in items),
        check=sum(i.state == "check" for i in items),
        todo=sum(i.state == "todo" for i in items),
    )
    return ProjectReadiness(ready=summary.todo == 0, summary=summary, items=items)


# ───────────────────────────── loading ─────────────────────────────


def _deployment_provides(capability: str) -> bool:
    """`deployment_default_type` when the registry has it (S40-05), else the fixed list."""
    from choregos_adapters import registry

    custom = getattr(registry, "deployment_default_type", None)
    if callable(custom):
        return custom(capability) is not None
    return capability in PROVIDED_BY_THE_DEPLOYMENT


async def _connector_facts(session: AsyncSession, project: Project) -> dict[str, ConnectorFact]:
    from choregos_adapters import spec_of
    from choregos_adapters.registry import references_de_secret

    from ..secret_store import secret_states

    rows = (
        (await session.execute(select(Connector).where(Connector.project_id == project.id))).scalars().all()
    )
    facts: dict[str, ConnectorFact] = {}
    for row in rows:
        spec = spec_of(row.kind, row.type)
        expected = tuple(s for s in (spec.secret_fields if spec else ()) if s not in OPTIONAL_SECRETS)
        references = references_de_secret(row.kind, row.type, dict(row.secret_refs or {}), row.secret_ref)
        states = await secret_states(session, project.org_id, references)
        is_set = frozenset(
            s.field
            for s in states
            if s.source == "reference" or (s.source == "store" and s.version is not None)
        )
        facts[row.kind] = ConnectorFact(
            kind=row.kind,
            type=row.type,
            status=row.status or "unknown",
            last_check_at=row.last_check_at,
            last_error=row.last_error,
            config=dict(row.config or {}),
            secrets_expected=expected,
            secrets_set=is_set,
        )
    return facts


async def _github_owners(
    session: AsyncSession, project: Project, connectors: dict[str, ConnectorFact]
) -> tuple[tuple[str, str, bool], ...]:
    owners: list[tuple[str, str, bool]] = []
    for kind, type_ in GITHUB_KINDS.items():
        connector = connectors.get(kind)
        if connector is None or connector.type != type_ or connector.config.get("api_url"):
            continue
        owner = github_app.owner_of(str(connector.config.get("repo") or ""))
        if owner is None:
            continue
        claimed = await github_app.claimed_installation_for(session, project.org_id, owner) is not None
        owners.append((kind, owner, claimed))
    return tuple(owners)


async def _usable_backends(session: AsyncSession) -> frozenset[str]:
    from ..routers.admin import KNOWN_BACKENDS

    rows = {row.name: row for row in (await session.execute(select(BackendRegistryRow))).scalars().all()}
    allowed = set(get_settings().allowed_agent_backends)
    usable = {
        name
        for name, _capabilities in KNOWN_BACKENDS
        if backend_enabled(name, rows[name].enabled if name in rows else None)
        and (not allowed or name in allowed)
    }
    return frozenset(usable)


def _model_facts(
    workflows: list[Any], config: ProjectConfig, validated: dict[str, list[str]]
) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    """(unknown profiles, unvalidated profiles, how many profiles the workflows name)."""
    requested: set[str] = set()
    for workflow in workflows:
        for actor in workflow.actors.values():
            model = str(getattr(actor, "model", "") or "")
            if model.startswith("profile:") and model != "profile:by_size":
                requested.add(model)
    resolver = ModelResolver(gateway_url="", gateway_models=[], validated_backends=validated)
    backend = config.agent.default_backend
    unknown: list[str] = []
    unvalidated: list[str] = []
    for model in sorted(requested):
        try:
            resolver.resolve(model, project=config, backend=backend, allow_unvalidated=True)
        except ModelResolutionError:
            unknown.append(model.removeprefix("profile:"))
            continue
        try:
            resolver.resolve(model, project=config, backend=backend, allow_unvalidated=False)
        except ModelResolutionError:
            unvalidated.append(model.removeprefix("profile:"))
    return tuple(unknown), tuple(unvalidated), len(requested)


#: How long a probe's answer is reused: a readiness page is read often, a bucket HEAD need not be.
PROBE_TTL = 60.0
_probe: tuple[float, ObjectStoreFact] | None = None


def forget_object_store_probe() -> None:
    global _probe  # noqa: PLW0603 - one cached answer per process, like the store itself
    _probe = None


async def probe_object_store() -> ObjectStoreFact:
    """Asks the store whether it answers; the answer is cached for `PROBE_TTL` seconds."""
    global _probe  # noqa: PLW0603 - one cached answer per process, like the store itself
    if _probe is not None and time.monotonic() - _probe[0] < PROBE_TTL:
        return _probe[1]
    try:
        await object_store().check()
        fact = ObjectStoreFact()
    except (ObjectStoreError, ValueError) as exc:
        fact = ObjectStoreFact(error=str(exc))
    _probe = (time.monotonic(), fact)
    return fact


def runtime_fact(connectors: dict[str, ConnectorFact], policy: Any) -> RuntimeFact | None:
    """Does the warm pool run the RuntimeClass the policy asks for? Judged only when it can be.

    No judgment (None) when the policy asks for no gVisor (the orchestrator then gives a run the
    pool's own class), when the executor in use is not the warm pool (a Job sets `runtimeClassName`
    per run), under fakes, or when the deployment never told the API its pool's class
    (`CHOREGOS_RUNNER_RUNTIME_CLASS` unset; an empty value means "the cluster default")."""
    import os

    from choregos_adapters import fakes_enabled
    from choregos_adapters.executor.agent_sandbox import runtime_class_refusal
    from choregos_adapters.registry import default_executor_kind

    # `classe_d_execution` (orchestrator): a `gvisor` policy is never bypassed, nothing else is asked.
    if policy.sandbox.runtime != "gvisor" or fakes_enabled():
        return None
    runtime = connectors.get("runtime")
    if (runtime.type if runtime else default_executor_kind()) != "agent_sandbox":
        return None
    config = runtime.config if runtime else {}
    pool = config.get("runtime_class", os.environ.get("CHOREGOS_RUNNER_RUNTIME_CLASS"))
    if pool is None:
        return None
    warm_pool = config.get("warm_pool") or os.environ.get("CHOREGOS_SANDBOX_WARM_POOL") or "choregos-runner"
    return RuntimeFact("gvisor", runtime_class_refusal("gvisor", str(pool), str(warm_pool)))


async def template_workflow_facts(
    session: AsyncSession, template_ref: str | None, rows: list[Any], org_id: str | None = None
) -> tuple[TemplateWorkflowFact, ...]:
    """The project's active `source: template` workflows that its template still ships, both sides
    checksummed now. Nothing (an empty tuple) when there is no template, when it cannot be read any
    more, or when a document no longer parses: the item then says nothing rather than guess."""
    if not template_ref:
        return ()
    try:
        shipped: dict[str, tuple[str, str]] = {}
        for ref, source in shipped_workflows(*await manifeste_du_gabarit(session, template_ref, org_id)):
            parsed, _ = parse_workflow(source, strict=False)
            shipped[parsed.metadata.name] = (ref, checksum(parsed))
    except (ApiError, WorkflowInvalid, OSError, yaml.YAMLError):
        return ()
    facts: list[TemplateWorkflowFact] = []
    for row in rows:
        if row.source != "template" or row.name not in shipped or not row.yaml:
            continue
        try:
            stored = checksum(parse_workflow(row.yaml, strict=False)[0])
        except (WorkflowInvalid, yaml.YAMLError):
            continue
        ref, shipped_checksum = shipped[row.name]
        facts.append(TemplateWorkflowFact(row.name, row.version, stored, shipped_checksum, ref))
    return tuple(sorted(facts, key=lambda f: f.name))


def _file_outputs(workflows: list[Any]) -> tuple[str, ...]:
    names: set[str] = set()
    for workflow in workflows:
        for transition in workflow.transitions:
            for name, type_ in transition.output_types.items():
                declared = ARTIFACT_TYPES.get(type_)
                if declared is not None and not declared.inline:
                    names.add(name)
    return tuple(sorted(names))


async def load_facts(session: AsyncSession, project: Project, org_slug: str) -> Facts:
    from choregos_core.dsl.exigences import exigences

    config = ProjectConfig.model_validate(project.config)
    workflow_rows = (
        (
            await session.execute(
                select(WorkflowDef).where(
                    WorkflowDef.project_id == project.id, WorkflowDef.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    workflows = [workflow_model(row) for row in workflow_rows]
    requirements = tuple((e.capacite, tuple(e.raisons)) for e in exigences(workflows))
    connectors = await _connector_facts(session, project)
    policy = policy_model(await active_policy(session, project.id))
    known_groups = await groups_known_in(session, project.org_id)
    unknown_groups = sorted(
        {
            str(issue["message"]).split("`")[1]
            for workflow in workflows
            for issue in unknown_group_warnings(workflow, known_groups)
        }
    )
    memberships = (
        (
            await session.execute(
                select(Membership.role).where(
                    Membership.org_id == project.org_id,
                    (Membership.project_id == project.id) | Membership.project_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    validated = {
        row.litellm_model: list(row.validated_backends or [])
        for row in (
            await session.execute(select(ModelProfileRow).where(ModelProfileRow.scope == "platform"))
        ).scalars()
    }
    unknown_profiles, unvalidated_profiles, used = _model_facts(workflows, config, validated)
    state = project.provision_state or {}
    failed = next((s for s in state.get("steps", []) if s.get("status") == "failed"), None)
    file_outputs = _file_outputs(workflows)
    capabilities = {c for c, _ in requirements} | set(connectors)
    return Facts(
        file_outputs=file_outputs,
        object_store=await probe_object_store() if file_outputs else None,
        runtime=runtime_fact(connectors, policy),
        template=project.template_ref or None,
        template_workflows=await template_workflow_facts(
            session, project.template_ref, list(workflow_rows), project.org_id
        ),
        adopted=await adopted_facts(session, project.org_id, list(workflow_rows)),
        slug=project.slug,
        status=project.status,
        provision_failure=(f"{failed.get('name')}: {failed.get('message')}" if failed else None),
        config=config,
        workflows=tuple(row.name for row in workflow_rows),
        default_workflow=project.default_workflow,
        requirements=requirements,
        connectors=connectors,
        provided=frozenset(c for c in capabilities if _deployment_provides(c)),
        github_app=github_app.get_github_app() is not None,
        github_owners=await _github_owners(session, project, connectors),
        usable_backends=await _usable_backends(session),
        profiles_unknown=unknown_profiles,
        profiles_unvalidated=unvalidated_profiles,
        profiles_used=used,
        ticket_budget_set=bool(policy.budgets.ticket_usd),
        daily_budget_set=policy.budgets.daily_project_usd is not None,
        has_owner="project_owner" in memberships,
        has_release_captain="release_captain" in memberships,
        unknown_groups=tuple(unknown_groups),
        has_release_train=await declares_a_release_train(session, project.id),
        announces=bool(policy.findings.notify_severities) or policy.memory.enabled,
        work_items=(
            await session.execute(
                select(func.count()).select_from(WorkItem).where(WorkItem.project_id == project.id)
            )
        ).scalar_one(),
    )


async def project_readiness(session: AsyncSession, project: Project, org_slug: str) -> ProjectReadiness:
    return evaluate(await load_facts(session, project, org_slug))
