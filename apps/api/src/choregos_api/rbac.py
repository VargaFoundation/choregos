# SPDX-License-Identifier: Apache-2.0
"""RBAC : cinq rôles, une matrice de permissions explicite."""

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
    #: Changer les groupes d'un projet, donc ce que le catalogue lui ouvre (ADR 0014, second
    #: verrou). L'administrateur de l'organisation seul : un projet choisit ce dont il se sert,
    #: il ne choisit pas ce à quoi il a droit.
    TOOLS_GRANT = "tools:grant"
    PLATFORM_ADMIN = "platform:admin"
    AUDIT_READ = "audit:read"
    #: Créer un agent, publier ses versions, le suspendre ou le révoquer (ADR 0033) :
    #: l'administrateur de l'organisation. Un projet n'épingle qu'une version existante.
    AGENT_MANAGE = "agent:manage"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.PROJECT_READ}),
    Role.DEVELOPER: frozenset(
        {
            Permission.PROJECT_READ,
            Permission.ITEM_DECIDE,
            Permission.ITEM_CONTROL,
            Permission.RUN_REPLAY,
            Permission.FINDING_TRIAGE,
        }
    ),
    Role.RELEASE_CAPTAIN: frozenset(
        {
            Permission.PROJECT_READ,
            Permission.ITEM_DECIDE,
            Permission.ITEM_CONTROL,
            Permission.RUN_REPLAY,
            Permission.FINDING_TRIAGE,
            Permission.TRAIN_OPERATE,
            Permission.TRAIN_APPROVE,
        }
    ),
    Role.PROJECT_OWNER: frozenset(
        {
            Permission.PROJECT_READ,
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
        }
    ),
    Role.ORG_ADMIN: frozenset(Permission),
}


@dataclass(slots=True)
class Principal:
    """L'identité qui agit : un humain, un jeton d'API, ou le système."""

    user_id: str
    email: str
    display_name: str = ""
    kind: str = "user"
    org_roles: dict[str, Role] = field(default_factory=dict)
    #: Indexés par `org/slug`, JAMAIS par slug seul : deux organisations peuvent avoir un
    #: projet du même nom (`UniqueConstraint(org_id, slug)`), et un rôle sur `a/billing`
    #: donnait le même rôle sur `b/billing` — franchissement de locataire, état des lieux
    #: du 2026-09-24.
    project_roles: dict[str, Role] = field(default_factory=dict)
    groups: list[str] = field(default_factory=list)
    #: Quand l'humain s'est authentifié (secondes epoch), lu dans la session. `None` pour un jeton
    #: d'API ou une session antérieure à la 0.10.0 : une porte qui exige une authentification
    #: récente les traite comme trop anciennes.
    authentifie_le: int | None = None

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
