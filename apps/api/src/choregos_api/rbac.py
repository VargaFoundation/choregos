# SPDX-License-Identifier: Apache-2.0
"""RBAC: five roles, an explicit permission matrix."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from choregos_contracts import Role


class Permission(StrEnum):
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    PROJECT_CREATE = "project:create"
    PROJECT_DELETE = "project:delete"
    CONNECTOR_WRITE = "connector:write"
    WORKFLOW_WRITE = "workflow:write"
    POLICY_WRITE = "policy:write"
    MODELS_WRITE = "models:write"
    ITEM_DECIDE = "item:decide"
    ITEM_CONTROL = "item:control"
    RUN_REPLAY = "run:replay"
    TRAIN_OPERATE = "train:operate"
    TRAIN_APPROVE = "train:approve"
    FINDING_TRIAGE = "finding:triage"
    MEMORY_WRITE = "memory:write"
    MEMBER_MANAGE = "member:manage"
    #: List who belongs to the organisation (S26-04): every role, but only where it holds.
    MEMBER_READ = "member:read"
    #: Change the groups of a project, hence what the catalogue opens to it (ADR 0014, second
    #: lock). The organisation administrator alone: a project chooses what it uses, it does not
    #: choose what it is entitled to.
    TOOLS_GRANT = "tools:grant"
    PLATFORM_ADMIN = "platform:admin"
    AUDIT_READ = "audit:read"
    #: Create an agent, publish its versions, suspend or revoke it (ADR 0033): the organisation
    #: administrator. A project only pins an existing version.
    AGENT_MANAGE = "agent:manage"
    #: Import a skill from a repository of the organisation's allowlist (S32-05, ADR 0050): a project
    #: owner may, since the allowlist is what the administrator decided to trust. Another source,
    #: or a new version of an existing skill, still needs `agent:manage`.
    SKILL_IMPORT = "skill:import"
    #: Create and keep a personal agent (S39-09, ADR 0061): every role that does work — a viewer-only
    #: member has none. Whoever holds it in the organisation or in any of its projects may.
    AGENT_CREATE_PERSONAL = "agent:create_personal"
    #: Create, publish and manage the agents that belong to ONE project (S39-09, ADR 0061), checked
    #: against that project. `agent:manage` keeps its meaning: organisation and common agents.
    AGENT_PROJECT_MANAGE = "agent:project_manage"
    #: Enable, disable and price the models of the organisation's catalogue, choose its mode and
    #: its tiers (S39-07, ADR 0060): what the organisation lets its agents use.
    MODEL_CATALOGUE_MANAGE = "model:catalogue:manage"
    #: Assistants (S63-15, ADR 0133 point 5). `manage` and `audit` are held through the organisation role
    #: (the administrator) and, per installation, through its owner groups; `use` is held only through the
    #: audience groups, so no organisation role carries it. `services.assistant_access` decides.
    ASSISTANT_MANAGE = "assistant:manage"
    ASSISTANT_USE = "assistant:use"
    ASSISTANT_AUDIT = "assistant:audit"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.PROJECT_READ, Permission.MEMBER_READ}),
    Role.DEVELOPER: frozenset(
        {
            Permission.PROJECT_READ,
            Permission.MEMBER_READ,
            Permission.ITEM_DECIDE,
            Permission.ITEM_CONTROL,
            Permission.RUN_REPLAY,
            Permission.FINDING_TRIAGE,
            Permission.AGENT_CREATE_PERSONAL,
        }
    ),
    Role.RELEASE_CAPTAIN: frozenset(
        {
            Permission.PROJECT_READ,
            Permission.MEMBER_READ,
            Permission.ITEM_DECIDE,
            Permission.ITEM_CONTROL,
            Permission.RUN_REPLAY,
            Permission.FINDING_TRIAGE,
            Permission.TRAIN_OPERATE,
            Permission.TRAIN_APPROVE,
            Permission.AGENT_CREATE_PERSONAL,
        }
    ),
    Role.PROJECT_OWNER: frozenset(
        {
            Permission.PROJECT_READ,
            Permission.MEMBER_READ,
            Permission.PROJECT_WRITE,
            Permission.CONNECTOR_WRITE,
            Permission.WORKFLOW_WRITE,
            Permission.POLICY_WRITE,
            Permission.MODELS_WRITE,
            Permission.ITEM_DECIDE,
            Permission.ITEM_CONTROL,
            Permission.RUN_REPLAY,
            Permission.TRAIN_OPERATE,
            Permission.FINDING_TRIAGE,
            Permission.MEMORY_WRITE,
            Permission.MEMBER_MANAGE,
            Permission.AUDIT_READ,
            Permission.SKILL_IMPORT,
            Permission.AGENT_CREATE_PERSONAL,
            Permission.AGENT_PROJECT_MANAGE,
        }
    ),
    Role.ORG_ADMIN: frozenset(Permission) - {Permission.ASSISTANT_USE},
}


@dataclass(slots=True)
class Principal:
    """The identity that acts: a human, an API token, or the system."""

    user_id: str
    email: str
    display_name: str = ""
    kind: str = "user"
    org_roles: dict[str, Role] = field(default_factory=dict)
    #: Keyed by `org/slug`, NEVER by slug alone: two organisations may have a project of the same
    #: name (`UniqueConstraint(org_id, slug)`), and a role on `a/billing` gave the same role on
    #: `b/billing`, a tenant crossing (inventory of 2026-09-24).
    project_roles: dict[str, Role] = field(default_factory=dict)
    groups: list[str] = field(default_factory=list)
    #: When the human authenticated (epoch seconds), read from the session. `None` for an API token
    #: or a session older than 0.10.0: a gate that demands a recent authentication treats them as
    #: too old.
    authenticated_at: int | None = None
    #: The browser session the request came with (ADR 0130), `None` for an API token or a cookie
    #: opened before sessions were recorded: what My account calls "this session".
    session_id: str | None = None

    def role_for(self, org: str, project_slug: str | None = None) -> Role | None:
        if project_slug:
            role = self.project_roles.get(f"{org}/{project_slug}")
            if role is not None:
                return role
        return self.org_roles.get(org)

    def permissions(self, org: str, project_slug: str | None = None) -> frozenset[Permission]:
        role = self.role_for(org, project_slug)
        if role is None:
            return frozenset()
        base = set(ROLE_PERMISSIONS.get(role, frozenset()))
        org_role = self.org_roles.get(org)
        if org_role is not None and org_role != role:
            base |= set(ROLE_PERMISSIONS.get(org_role, frozenset()))
        return frozenset(base)

    def can(self, permission: Permission, org: str, project_slug: str | None = None) -> bool:
        return permission in self.permissions(org, project_slug)

    def is_platform_admin(self) -> bool:
        return any(role == Role.ORG_ADMIN for role in self.org_roles.values())


SYSTEM = Principal(user_id="system", email="system@choregos", display_name="Choregos", kind="system")
