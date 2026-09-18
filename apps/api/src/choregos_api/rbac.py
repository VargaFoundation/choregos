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
    PLATFORM_ADMIN = "platform:admin"
    AUDIT_READ = "audit:read"


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
    project_roles: dict[str, Role] = field(default_factory=dict)
    groups: list[str] = field(default_factory=list)

    def role_for(self, org: str, project_slug: str | None = None) -> Role | None:
        if project_slug and project_slug in self.project_roles:
            return self.project_roles[project_slug]
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
