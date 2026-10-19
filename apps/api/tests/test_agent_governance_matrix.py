"""The agent permission matrix of ADR 0061, cell by cell.

`decide()` answers every question the routers ask about who may do what with an agent. The expected
answers below are written out as the list of principals each cell lets through — nothing is derived
from the implementation — and every other principal must be refused, with a reason. Removing or
loosening one rule turns a cell red.
"""

from __future__ import annotations

import pytest
from choregos_api.rbac import ROLE_PERMISSIONS, Permission, Principal
from choregos_api.services.agent_governance import (
    TABLE,
    Action,
    Subject,
    decide,
    usable_in_project,
)
from choregos_contracts import Role

ORG = "varga"


def _who(user: str, org: Role | None = None, **projects: Role) -> Principal:
    return Principal(
        user_id=user,
        email=f"{user}@varga.dev",
        org_roles={ORG: org} if org else {},
        project_roles={f"{ORG}/{slug}": role for slug, role in projects.items()},
    )


PRINCIPALS: dict[str, Principal] = {
    "admin": _who("u-admin", Role.ORG_ADMIN),
    # owner of `dev` only, then of `ops` only: no organisation role
    "owner_dev": _who("u-owner-dev", dev=Role.PROJECT_OWNER),
    "owner_ops": _who("u-owner-ops", ops=Role.PROJECT_OWNER),
    # the owner of the personal agent below: an organisation developer
    "developer": _who("u-dev", Role.DEVELOPER),
    # the same person, who also owns `dev`
    "self_owner_dev": _who("u-dev", Role.DEVELOPER, dev=Role.PROJECT_OWNER),
    "other_dev": _who("u-other", Role.DEVELOPER),
    "viewer": _who("u-viewer", Role.VIEWER),
    "outsider": _who("u-outsider"),
}

PLATFORM = Subject(scope="platform", slug="developer")
ORG_AGENT = Subject(scope="org", slug="reviewer")
PROJECT_DEV = Subject(scope="project", project_slug="dev", slug="dev-helper")
PERSONAL = Subject(scope="personal", owner_id="u-dev", slug="my-agent")
SUBJECTS = {"platform": PLATFORM, "org": ORG_AGENT, "project": PROJECT_DEV, "personal": PERSONAL}

EVERYONE_IN = {"admin", "owner_dev", "owner_ops", "developer", "self_owner_dev", "other_dev", "viewer"}
ADMIN = {"admin"}
DEV_AND_ABOVE = EVERYONE_IN - {"viewer"}
# the organisation roles reach every project; a project role only its own
MEMBERS_OF_DEV = {"admin", "owner_dev", "developer", "self_owner_dev", "other_dev", "viewer"}

# (action, subject, keyword arguments of decide) -> the principals let through
CELLS: list[tuple[Action, str, dict[str, str | None], set[str]]] = [
    # see
    (Action.SEE, "platform", {}, EVERYONE_IN),
    (Action.SEE, "org", {}, EVERYONE_IN),
    (Action.SEE, "project", {}, MEMBERS_OF_DEV),
    (Action.SEE, "personal", {}, {"admin", "developer", "self_owner_dev"}),
    # publish, edit, life — and manage: the same people
    (Action.PUBLISH, "platform", {}, ADMIN),
    (Action.PUBLISH, "org", {}, ADMIN),
    (Action.PUBLISH, "project", {}, {"admin", "owner_dev", "self_owner_dev"}),
    (Action.PUBLISH, "personal", {}, {"admin", "developer", "self_owner_dev"}),
    (Action.MANAGE, "platform", {}, ADMIN),
    (Action.MANAGE, "org", {}, ADMIN),
    (Action.MANAGE, "project", {}, {"admin", "owner_dev", "self_owner_dev"}),
    (Action.MANAGE, "personal", {}, {"admin", "developer", "self_owner_dev"}),
    # release (S40-02, ADR 0071): the same people as publish
    (Action.RELEASE, "platform", {}, ADMIN),
    (Action.RELEASE, "org", {}, ADMIN),
    (Action.RELEASE, "project", {}, {"admin", "owner_dev", "self_owner_dev"}),
    (Action.RELEASE, "personal", {}, {"admin", "developer", "self_owner_dev"}),
    # create (no agent yet: the scope asked)
    (Action.CREATE, "none", {"target_scope": "org"}, ADMIN),
    (Action.CREATE, "none", {"target_scope": "platform"}, ADMIN),
    (
        Action.CREATE,
        "none",
        {"target_scope": "project", "project_slug": "dev"},
        {"admin", "owner_dev", "self_owner_dev"},
    ),
    (Action.CREATE, "none", {"target_scope": "project", "project_slug": "ops"}, {"admin", "owner_ops"}),
    (Action.CREATE, "none", {"target_scope": "personal"}, DEV_AND_ABOVE),
    # pin
    (Action.PIN, "platform", {"project_slug": "dev"}, {"admin", "owner_dev", "self_owner_dev"}),
    (Action.PIN, "platform", {"project_slug": "ops"}, {"admin", "owner_ops"}),
    (Action.PIN, "org", {"project_slug": "dev"}, {"admin", "owner_dev", "self_owner_dev"}),
    (Action.PIN, "project", {"project_slug": "dev"}, {"admin", "owner_dev", "self_owner_dev"}),
    (Action.PIN, "project", {"project_slug": "ops"}, set()),
    (Action.PIN, "personal", {"project_slug": "dev"}, set()),
    (Action.PIN, "personal", {"project_slug": "ops"}, set()),
    # trial
    (
        Action.TRIAL,
        "platform",
        {"project_slug": "dev"},
        {"admin", "owner_dev", "developer", "self_owner_dev", "other_dev"},
    ),
    (
        Action.TRIAL,
        "org",
        {"project_slug": "ops"},
        {"admin", "owner_ops", "developer", "self_owner_dev", "other_dev"},
    ),
    (
        Action.TRIAL,
        "project",
        {"project_slug": "dev"},
        {"admin", "owner_dev", "developer", "self_owner_dev", "other_dev"},
    ),
    (Action.TRIAL, "project", {"project_slug": "ops"}, set()),
    (Action.TRIAL, "personal", {"project_slug": "dev"}, {"developer", "self_owner_dev"}),
    (
        Action.TRIAL,
        "none",
        {"project_slug": "dev"},
        {"admin", "owner_dev", "developer", "self_owner_dev", "other_dev"},
    ),
    # fork: see the source AND create in the target scope
    (Action.FORK, "platform", {"target_scope": "org"}, ADMIN),
    (Action.FORK, "platform", {"target_scope": "platform"}, set()),
    (
        Action.FORK,
        "platform",
        {"target_scope": "project", "project_slug": "dev"},
        {"admin", "owner_dev", "self_owner_dev"},
    ),
    (Action.FORK, "platform", {"target_scope": "project", "project_slug": "ops"}, {"admin", "owner_ops"}),
    (Action.FORK, "platform", {"target_scope": "personal"}, DEV_AND_ABOVE),
    (Action.FORK, "org", {"target_scope": "personal"}, DEV_AND_ABOVE),
    (Action.FORK, "project", {"target_scope": "org"}, ADMIN),
    (
        Action.FORK,
        "project",
        {"target_scope": "project", "project_slug": "dev"},
        {"admin", "owner_dev", "self_owner_dev"},
    ),
    (
        Action.FORK,
        "project",
        {"target_scope": "personal"},
        {"admin", "owner_dev", "developer", "self_owner_dev", "other_dev"},
    ),
    (Action.FORK, "personal", {"target_scope": "personal"}, {"admin", "developer", "self_owner_dev"}),
    (Action.FORK, "personal", {"target_scope": "org"}, ADMIN),
    (
        Action.FORK,
        "personal",
        {"target_scope": "project", "project_slug": "dev"},
        {"admin", "self_owner_dev"},
    ),
    # move (promote)
    (Action.MOVE, "platform", {"target_scope": "org"}, set()),
    (Action.MOVE, "platform", {"target_scope": "project", "project_slug": "dev"}, set()),
    (Action.MOVE, "org", {"target_scope": "platform"}, set()),
    (Action.MOVE, "org", {"target_scope": "project", "project_slug": "dev"}, ADMIN),
    (Action.MOVE, "org", {"target_scope": "personal"}, set()),
    (Action.MOVE, "org", {"target_scope": "org"}, set()),
    (Action.MOVE, "project", {"target_scope": "org"}, ADMIN),
    (Action.MOVE, "project", {"target_scope": "project", "project_slug": "ops"}, set()),
    (Action.MOVE, "project", {"target_scope": "personal"}, set()),
    (Action.MOVE, "personal", {"target_scope": "org"}, ADMIN),
    (
        Action.MOVE,
        "personal",
        {"target_scope": "project", "project_slug": "dev"},
        {"admin", "self_owner_dev"},
    ),
    (Action.MOVE, "personal", {"target_scope": "project", "project_slug": "ops"}, {"admin"}),
    (Action.MOVE, "personal", {"target_scope": "platform"}, set()),
]


def _id(cell: tuple[Action, str, dict[str, str | None], set[str]]) -> str:
    action, subject, kwargs, _ = cell
    return f"{action}-{subject}-" + "-".join(f"{v}" for v in kwargs.values())


@pytest.mark.parametrize("cell", CELLS, ids=_id)
def test_each_cell_of_the_matrix(cell: tuple[Action, str, dict[str, str | None], set[str]]) -> None:
    action, subject, kwargs, allowed = cell
    wrong = []
    for name, principal in PRINCIPALS.items():
        decision = decide(principal, ORG, SUBJECTS.get(subject), action, **kwargs)  # type: ignore[arg-type]
        if decision.allowed != (name in allowed):
            wrong.append(f"{name}: {'allowed' if decision.allowed else 'refused'} ({decision.reason})")
        if not decision.allowed:
            assert decision.reason, f"{name} is refused with no reason"
    assert not wrong, "\n".join(wrong)


def test_every_action_has_its_rule_and_its_cells() -> None:
    assert set(TABLE) == set(Action)
    assert {cell[0] for cell in CELLS} == set(Action), "an action has no cell in the matrix"
    for scope in ("platform", "org", "project", "personal"):
        assert {cell[0] for cell in CELLS if cell[1] == scope} >= {Action.SEE, Action.PUBLISH, Action.RELEASE}


def test_a_pin_that_cannot_be_is_unprocessable_not_forbidden() -> None:
    personal = decide(PRINCIPALS["admin"], ORG, PERSONAL, Action.PIN, project_slug="dev")
    foreign = decide(PRINCIPALS["admin"], ORG, PROJECT_DEV, Action.PIN, project_slug="ops")
    rights = decide(PRINCIPALS["developer"], ORG, ORG_AGENT, Action.PIN, project_slug="dev")
    assert personal.unprocessable and "personal" in (personal.reason or "")
    assert foreign.unprocessable and "dev" in (foreign.reason or "")
    assert not rights.allowed and not rights.unprocessable, "a missing right is a 403"


def test_a_refusal_on_a_common_agent_points_to_the_fork() -> None:
    reason = decide(PRINCIPALS["owner_dev"], ORG, PLATFORM, Action.PUBLISH).reason or ""
    assert "common agent" in reason and "fork" in reason


def test_the_new_permissions_follow_the_roles() -> None:
    personal = {role for role, perms in ROLE_PERMISSIONS.items() if Permission.AGENT_CREATE_PERSONAL in perms}
    project = {role for role, perms in ROLE_PERMISSIONS.items() if Permission.AGENT_PROJECT_MANAGE in perms}
    assert personal == {Role.DEVELOPER, Role.RELEASE_CAPTAIN, Role.PROJECT_OWNER, Role.ORG_ADMIN}
    assert project == {Role.PROJECT_OWNER, Role.ORG_ADMIN}


def test_where_an_agent_may_run() -> None:
    assert usable_in_project("platform", None, "a", "p1") is None
    assert usable_in_project("org", None, "a", "p1") is None
    assert usable_in_project("project", "p1", "a", "p1") is None
    assert "another project" in (usable_in_project("project", "p2", "a", "p1") or "")
    assert "personal" in (usable_in_project("personal", None, "a", "p1") or "")
