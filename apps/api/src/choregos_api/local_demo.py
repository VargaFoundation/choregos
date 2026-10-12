# SPDX-License-Identifier: Apache-2.0
"""The demo organisation of the local install (`compose.yaml`, S28): one project, ready to run.

    python -m choregos_api.local_demo

The one-shot `seed` service of the compose file runs it after the API is healthy, so that the
API's own bootstrap (`CHOREGOS_BOOTSTRAP_ORG`, `amorcage.py`) has already created the organisation
and its admin. It writes to the database like `demo/seed.py` does: provisioning a project means
Kubernetes namespaces and GitOps, which a laptop install does not have.

Re-running it changes nothing that exists: a connector changed in the console stays changed.

Fakes mode (no model key): the tracker is Choregos itself (`internal`), the agent is simulated
(`fake` runtime with `simulate`: every declared output filled, no model called), the rest are
in-memory fakes. The work item still goes through the real path: Temporal, the database, the
gates, the human decision.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from .config import Settings, get_settings
from .db.models import Connector, Organization, Project
from .db.session import session_scope
from .logging import configure_logging, get_logger

logger = get_logger("choregos.local_demo")

DEMO_PROJECT = "demo"

#: The demo project's workflow: a request comes in, an agent writes a plan, a person approves it.
#: No repository, no deployment: nothing a laptop does not have.
DEMO_WORKFLOW = """\
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: local-demo
  version: 1
  description: A request comes in, an agent writes a plan, a person approves it.
actors:
  planner: { type: agent, role: plan, model: "profile:standard", max_turns: 20, max_minutes: 10 }
  approver: { type: human, group: approvers, sla_hours: 24 }
states:
  inbox: { display: New request, kind: wait }
  awaiting_approval: { display: Plan to approve, kind: wait }
  approved: { display: Approved, terminal: true }
  needs_human: { display: Needs a human, kind: wait }
  abandoned: { display: Abandoned, terminal: true }
transitions:
  - id: t-plan
    from: inbox
    to: awaiting_approval
    by: planner
    outputs: [plan_markdown]
    gates: [outputs_present]
    on_fail: { to: inbox, max_attempts: 2, escalate_to: needs_human }
  - id: t-approve
    from: awaiting_approval
    to: approved
    by: approver
    on_reject: inbox
    timeout_hours: 72
  - id: t-abandon
    from: needs_human
    to: abandoned
    by: approver
    timeout_hours: 168
defaults:
  from_any_agent_state:
    on_question: needs_human
    on_budget_exceeded: needs_human
    on_timeout: needs_human
  needs_human:
    on_answer: resume
    on_abandon: abandoned
"""


def demo_connectors() -> dict[str, tuple[str, dict[str, Any]]]:
    """The demo project's connectors, by kind: `(type, config)`."""
    return {
        "tracker": ("internal", {}),
        "scm": ("fake", {}),
        "ci": ("fake", {}),
        "cd": ("fake", {}),
        "runtime": ("fake", {"simulate": True}),
        "gateway": ("fake", {}),
        "memory": ("lexical", {}),
        "notify": ("fake", {}),
    }


async def seed_local_demo(settings: Settings | None = None) -> dict[str, list[str]]:
    """Create what is missing of the demo organisation and project; return what was created."""
    from .rbac import SYSTEM
    from .services import ensure_defaults, publier_workflow

    settings = settings or get_settings()
    org_slug = settings.bootstrap_org or "demo"
    created: dict[str, list[str]] = {"organisations": [], "projects": [], "connectors": []}
    async with session_scope(orgs="*") as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == org_slug))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(slug=org_slug, name=settings.bootstrap_org_name or org_slug)
            session.add(org)
            await session.flush()
            created["organisations"].append(org_slug)

        project = (
            await session.execute(
                select(Project).where(Project.org_id == org.id, Project.slug == DEMO_PROJECT)
            )
        ).scalar_one_or_none()
        if project is None:
            project = Project(
                org_id=org.id,
                slug=DEMO_PROJECT,
                name="Demo project",
                status="active",
                config={"slug": DEMO_PROJECT, "org": org_slug, "display_name": "Demo project"},
            )
            session.add(project)
            await session.flush()
            # The demo workflow first, as the default; `ensure_defaults` then only adds the policy.
            await publier_workflow(session, SYSTEM, project, DEMO_WORKFLOW, devient_le_defaut=True)
            await ensure_defaults(session, project)
            created["projects"].append(DEMO_PROJECT)

        existing = {
            c.kind: c
            for c in (
                await session.execute(select(Connector).where(Connector.project_id == project.id))
            ).scalars()
        }
        for kind, (type_name, config) in demo_connectors().items():
            if kind not in existing:
                session.add(
                    Connector(project_id=project.id, kind=kind, type=type_name, config=config, status="ok")
                )
                created["connectors"].append(kind)
        await session.flush()
    logger.info("local demo seeded", organisation=org_slug, project=DEMO_PROJECT, **created)
    return created


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    asyncio.run(seed_local_demo(settings))


if __name__ == "__main__":
    main()
