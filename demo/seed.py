#!/usr/bin/env python3
"""Sets up the demonstration: one organisation, two projects, their tickets.

Writes straight to the database, like `dev/scripts/seed.py`: the platform normally receives
its tickets from a tracker (GitHub, Jira), and the demonstration has none — it is the only
thing it simulates. Everything else is the real path: workflow, agents, guarantees.

    python demo/seed.py            # creates what is missing, leaves what exists alone
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent

# Two ways of giving the agents a model, chosen by DEMO_GATEWAY:
#
# - `litellm` (default): the embedded gateway mints a key PER RUN, capped, and counts the
#   spend. The projects then name gateway ALIASES (`platform/standard`), and the resolver
#   reads the real model behind them to check that `claude-code` really gets a Claude. It is
#   the only mode where the cost ledger carries a figure.
# - `direct`: the agent brings its own credentials (a Claude Code subscription in
#   `agent-creds`). It talks to the provider DIRECTLY, so the name must be the model's
#   identifier at the provider (`claude-sonnet-5`), not the prefixed form LiteLLM expects
#   for routing. Nothing is counted, nothing is capped beyond turns and minutes.
GATEWAY = os.environ.get("DEMO_GATEWAY", "litellm")
if GATEWAY not in {"litellm", "direct"}:
    raise SystemExit(f"DEMO_GATEWAY={GATEWAY!r}: expected `litellm` or `direct`")
MODELS = (
    {
        "standard": "platform/standard",
        "strong": "platform/strong",
        "cheap": "platform/cheap",
        "by_size": "platform/standard",
    }
    if GATEWAY == "litellm"
    else {
        "standard": "claude-sonnet-5",
        "strong": "claude-sonnet-5",
        "cheap": "claude-haiku-4-5",
        "by_size": "claude-sonnet-5",
    }
)
# A ticket has only one interpreter (Temporal refuses a finished id): replaying the
# demonstration takes NEW tickets. `DEMO_SERIE=b` suffixes the keys (`DEMO-1b`).
SERIES = os.environ.get("DEMO_SERIE", "")


def workflow_path(name: str) -> pathlib.Path:
    """`demo/workflows/<name>` locally, `<name>` flat when a ConfigMap mounts them."""
    for candidate in (ROOT / "workflows" / name, ROOT / name):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"workflow not found: {name}")


PROJECTS = {
    "panier": {
        "name": "Cart (software demo)",
        "workflow": "demo-code.yaml",
        "initial_state": "inbox",
        "config": {
            "slug": "panier",
            "org": "demo",
            "repo": {
                # The cluster's own git: the demonstration borrows no credentials from outside.
                "url": "git://demo-git:9418/app.git",
                "default_branch": "main",
                "language": "python",
            },
            "models": {"profiles": MODELS},
        },
        # Connectors are separate rows (table `connectors`), not configuration.
        # `fake` for the tracker and the SCM: no GitHub on a bench. The rest is real.
        "connectors": {
            "tracker": ("internal", {}),
            "scm": ("fake", {}),
            "ci": ("fake", {}),
            "cd": ("fake", {}),
            "runtime": ("k8s_job", {}),
            "gateway": (GATEWAY, {}),
            "memory": ("ecphoria", {}),
            "notify": ("fake", {}),
        },
        "tickets": [
            (
                "DEMO-1",
                "The cart total ignores VAT",
                "`Panier.total_ht()` exists, but nothing computes the total including VAT.\n\n"
                "Add `total_ttc(taux)` with a default rate of 20 %, and its test.",
                "S",
            ),
            (
                "DEMO-2",
                "Cannot remove an item from the cart",
                "Add `Panier.retirer(libelle)`, which removes the first occurrence and "
                "raises `KeyError` if the item is absent. With its tests.",
                "S",
            ),
            (
                "DEMO-3",
                "Apply a percentage discount",
                "Add `Panier.appliquer_remise(pourcentage)`, which clamps the discount to "
                "[0, 100] and rejects anything else with `ValueError`. With its tests.",
                "M",
            ),
        ],
    },
    "staffing": {
        "name": "Staffing (business demo)",
        "workflow": "staffing.yaml",
        "initial_state": "demande",
        "config": {
            "slug": "staffing",
            "org": "demo",
            # NO REPOSITORY. This line of business has no use for one, and since 2026-09-24 it
            # is no longer obliged to declare one (ADR 0012, limit no. 1 — lifted). The agent
            # works in an empty directory: nothing to clone, nothing to push, and the
            # guarantees that read a diff refuse because they have nothing to read.
            "models": {"profiles": MODELS},
        },
        "connectors": {
            "tracker": ("internal", {}),
            "scm": ("fake", {}),
            "ci": ("fake", {}),
            "cd": ("fake", {}),
            "runtime": ("k8s_job", {}),
            "gateway": (GATEWAY, {}),
            "memory": ("ecphoria", {}),
            "notify": ("fake", {}),
        },
        "tickets": [
            (
                "RH-1",
                "Data project manager for a 6-month engagement",
                "Client: industry. Start within 4 weeks, 3 days on site in Lille.\n"
                "Expected: leading an analytics migration programme, business "
                "stakeholders, dbt knowledge appreciated. Target daily rate 650 EUR.",
                "M",
            ),
            (
                "RH-2",
                "Two Python developers as reinforcements",
                "3-month reinforcement on an existing FastAPI API. Autonomy expected, "
                "demanding code review, full remote work possible.",
                "M",
            ),
        ],
    },
}


async def main() -> int:
    from choregos_api.db.models import Connector, Organization, Project, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults
    from choregos_core.dsl.parser import checksum, parse_workflow
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == "demo"))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(slug="demo", name="Demonstration")
            session.add(org)
            await session.flush()

        for slug, spec in PROJECTS.items():
            project = (
                await session.execute(select(Project).where(Project.slug == slug))
            ).scalar_one_or_none()
            if project is None:
                project = Project(
                    org_id=org.id,
                    slug=slug,
                    name=spec["name"],
                    status="active",
                    config=spec["config"],
                )
                session.add(project)
                await session.flush()
                print(f"project {slug} created")
            else:
                project.config = spec["config"]
                print(f"project {slug} updated")

            for kind, (type_, config) in spec["connectors"].items():
                row = (
                    await session.execute(
                        select(Connector).where(Connector.project_id == project.id, Connector.kind == kind)
                    )
                ).scalar_one_or_none()
                if row is None:
                    session.add(
                        Connector(project_id=project.id, kind=kind, type=type_, config=config, status="ok")
                    )
                else:
                    row.type, row.config = type_, config
            await session.flush()

            source = workflow_path(str(spec["workflow"])).read_text(encoding="utf-8")
            workflow, report = parse_workflow(source, strict=False)
            if not report.valid:
                print(f"invalid workflow for {slug}: {report.errors}", file=sys.stderr)
                return 1
            # The project first gets its defaults (policy, the product's workflow), then
            # THIS workflow is installed in their place — which is what `PUT /workflow` does.
            await ensure_defaults(session, project)
            for previous in (
                await session.execute(
                    select(WorkflowDef).where(
                        WorkflowDef.project_id == project.id, WorkflowDef.is_active.is_(True)
                    )
                )
            ).scalars():
                previous.is_active = False
            # Replayable: (project, name, version) is unique. The existing one is updated
            # rather than a second inserted — otherwise a second run breaks on the
            # constraint, and the script would stop being replayable.
            definition = (
                await session.execute(
                    select(WorkflowDef).where(
                        WorkflowDef.project_id == project.id,
                        WorkflowDef.name == workflow.metadata.name,
                        WorkflowDef.version == workflow.metadata.version,
                    )
                )
            ).scalar_one_or_none()
            document = workflow.model_dump(mode="json", by_alias=True, exclude_none=True)
            if definition is None:
                definition = WorkflowDef(
                    project_id=project.id,
                    name=workflow.metadata.name,
                    version=workflow.metadata.version,
                    source="projet",  # lang: fr-ok persisted value, read back by the API
                )
                session.add(definition)
            definition.yaml = source
            definition.json_doc = document
            definition.checksum = checksum(workflow)
            definition.is_active = True
            await session.flush()

            for base_key, title, body, size in spec["tickets"]:
                key = f"{base_key}{SERIES}"
                existing = (
                    await session.execute(select(WorkItem).where(WorkItem.tracker_key == key))
                ).scalar_one_or_none()
                if existing is not None:
                    print(f"  ticket {key} already there ({existing.state})")
                    continue
                session.add(
                    WorkItem(
                        project_id=project.id,
                        tracker_key=key,
                        title=title,
                        body_snapshot=body,
                        state=spec["initial_state"],
                        size=size,
                        # A scope of PATHS only makes sense where there are project
                        # files. The seed used to impose it on every ticket, HR included:
                        # the HR agent, in its empty directory, stopped on "src/ and tests/
                        # do not exist" — and it was right to refuse rather than write
                        # outside what it had been told.
                        allowed_paths=["src/**", "tests/**"] if spec["config"].get("repo") else [],
                    )
                )
                print(f"  ticket {key} created")
    if "--start" in sys.argv:
        await start_tickets()
    return 0


async def start_tickets() -> None:
    """Starts each ticket's interpreter — what `mark_agent_ready` does in the API.

    A ticket put in the database does not move by itself: it is a Temporal workflow, one per
    ticket, that reads the state, picks the transition and launches the agent. Starting it
    here avoids inventing an entry point the platform does not have.
    """
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import get_temporal, interpreter_id
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        rows = (
            await session.execute(select(WorkItem, Project).join(Project, Project.id == WorkItem.project_id))
        ).all()
        for item, project in rows:
            if item.temporal_wf_id:
                print(f"  {item.tracker_key} already started")
                continue
            workflow_id = interpreter_id(project.slug, item.tracker_key)
            payload = {
                "project_id": project.id,
                "project_slug": project.slug,
                "work_item_id": item.id,
                "tracker_key": item.tracker_key,
            }
            try:
                await get_temporal().start_interpreter(workflow_id, payload)
                print(f"  {item.tracker_key} started ({workflow_id})")
            except Exception as exc:
                # A workflow of that name is already running: a ticket has only one
                # interpreter, and that is what guarantees it is not handled twice. The
                # script is replayable, it must not stop on this.
                if "already started" not in str(exc).lower():
                    raise
                print(f"  {item.tracker_key}: an interpreter is already running")
            item.temporal_wf_id = workflow_id


if __name__ == "__main__":
    os.environ.setdefault("CHOREGOS_ENV", "dev")
    raise SystemExit(asyncio.run(main()))
