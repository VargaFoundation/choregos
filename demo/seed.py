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

RACINE = pathlib.Path(__file__).resolve().parent

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
PASSERELLE = os.environ.get("DEMO_GATEWAY", "litellm")
if PASSERELLE not in {"litellm", "direct"}:
    raise SystemExit(f"DEMO_GATEWAY={PASSERELLE!r}: expected `litellm` or `direct`")
MODELES = (
    {
        "standard": "platform/standard",
        "strong": "platform/strong",
        "cheap": "platform/cheap",
        "by_size": "platform/standard",
    }
    if PASSERELLE == "litellm"
    else {
        "standard": "claude-sonnet-5",
        "strong": "claude-sonnet-5",
        "cheap": "claude-haiku-4-5",
        "by_size": "claude-sonnet-5",
    }
)
# A ticket has only one interpreter (Temporal refuses a finished id): replaying the
# demonstration takes NEW tickets. `DEMO_SERIE=b` suffixes the keys (`DEMO-1b`).
SERIE = os.environ.get("DEMO_SERIE", "")


def chemin_workflow(nom: str) -> pathlib.Path:
    """`demo/workflows/<nom>` locally, `<nom>` flat when a ConfigMap mounts them."""
    for candidat in (RACINE / "workflows" / nom, RACINE / nom):
        if candidat.exists():
            return candidat
    raise FileNotFoundError(f"workflow not found: {nom}")


PROJETS = {
    "panier": {
        "nom": "Cart (software demo)",
        "workflow": "demo-code.yaml",
        "etat_initial": "inbox",
        "config": {
            "slug": "panier",
            "org": "demo",
            "repo": {
                # The cluster's own git: the demonstration borrows no credentials from outside.
                "url": "git://demo-git:9418/app.git",
                "default_branch": "main",
                "language": "python",
            },
            "models": {"profiles": MODELES},
        },
        # Connectors are separate rows (table `connectors`), not configuration.
        # `fake` for the tracker and the SCM: no GitHub on a bench. The rest is real.
        "connecteurs": {
            "tracker": ("internal", {}),
            "scm": ("fake", {}),
            "ci": ("fake", {}),
            "cd": ("fake", {}),
            "runtime": ("k8s_job", {}),
            "gateway": (PASSERELLE, {}),
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
        "nom": "Staffing (business demo)",
        "workflow": "staffing.yaml",
        "etat_initial": "demande",
        "config": {
            "slug": "staffing",
            "org": "demo",
            # NO REPOSITORY. This line of business has no use for one, and since 2026-09-24 it
            # is no longer obliged to declare one (ADR 0012, limit no. 1 — lifted). The agent
            # works in an empty directory: nothing to clone, nothing to push, and the
            # guarantees that read a diff refuse because they have nothing to read.
            "models": {"profiles": MODELES},
        },
        "connecteurs": {
            "tracker": ("internal", {}),
            "scm": ("fake", {}),
            "ci": ("fake", {}),
            "cd": ("fake", {}),
            "runtime": ("k8s_job", {}),
            "gateway": (PASSERELLE, {}),
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

        for slug, spec in PROJETS.items():
            projet = (await session.execute(select(Project).where(Project.slug == slug))).scalar_one_or_none()
            if projet is None:
                projet = Project(
                    org_id=org.id,
                    slug=slug,
                    name=spec["nom"],
                    status="active",
                    config=spec["config"],
                )
                session.add(projet)
                await session.flush()
                print(f"project {slug} created")
            else:
                projet.config = spec["config"]
                print(f"project {slug} updated")

            for kind, (type_, config) in spec["connecteurs"].items():
                ligne = (
                    await session.execute(
                        select(Connector).where(Connector.project_id == projet.id, Connector.kind == kind)
                    )
                ).scalar_one_or_none()
                if ligne is None:
                    session.add(
                        Connector(project_id=projet.id, kind=kind, type=type_, config=config, status="ok")
                    )
                else:
                    ligne.type, ligne.config = type_, config
            await session.flush()

            source = chemin_workflow(str(spec["workflow"])).read_text(encoding="utf-8")
            workflow, rapport = parse_workflow(source, strict=False)
            if not rapport.valid:
                print(f"invalid workflow for {slug}: {rapport.errors}", file=sys.stderr)
                return 1
            # The project first gets its defaults (policy, the product's workflow), then
            # THIS workflow is installed in their place — which is what `PUT /workflow` does.
            await ensure_defaults(session, projet)
            for ancien in (
                await session.execute(
                    select(WorkflowDef).where(
                        WorkflowDef.project_id == projet.id, WorkflowDef.is_active.is_(True)
                    )
                )
            ).scalars():
                ancien.is_active = False
            # Replayable: (project, name, version) is unique. The existing one is updated
            # rather than a second inserted — otherwise a second run breaks on the
            # constraint, and the script would stop being replayable.
            defini = (
                await session.execute(
                    select(WorkflowDef).where(
                        WorkflowDef.project_id == projet.id,
                        WorkflowDef.name == workflow.metadata.name,
                        WorkflowDef.version == workflow.metadata.version,
                    )
                )
            ).scalar_one_or_none()
            document = workflow.model_dump(mode="json", by_alias=True, exclude_none=True)
            if defini is None:
                defini = WorkflowDef(
                    project_id=projet.id,
                    name=workflow.metadata.name,
                    version=workflow.metadata.version,
                    source="projet",
                )
                session.add(defini)
            defini.yaml = source
            defini.json_doc = document
            defini.checksum = checksum(workflow)
            defini.is_active = True
            await session.flush()

            for cle_de_base, titre, corps, taille in spec["tickets"]:
                cle = f"{cle_de_base}{SERIE}"
                deja = (
                    await session.execute(select(WorkItem).where(WorkItem.tracker_key == cle))
                ).scalar_one_or_none()
                if deja is not None:
                    print(f"  ticket {cle} already there ({deja.state})")
                    continue
                session.add(
                    WorkItem(
                        project_id=projet.id,
                        tracker_key=cle,
                        title=titre,
                        body_snapshot=corps,
                        state=spec["etat_initial"],
                        size=taille,
                        # A scope of PATHS only makes sense where there are project
                        # files. The seed used to impose it on every ticket, HR included:
                        # the HR agent, in its empty directory, stopped on "src/ and tests/
                        # do not exist" — and it was right to refuse rather than write
                        # outside what it had been told.
                        allowed_paths=["src/**", "tests/**"] if spec["config"].get("repo") else [],
                    )
                )
                print(f"  ticket {cle} created")
    if "--demarrer" in sys.argv:
        await demarrer()
    return 0


async def demarrer() -> None:
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
        for item, projet in rows:
            if item.temporal_wf_id:
                print(f"  {item.tracker_key} already started")
                continue
            workflow_id = interpreter_id(projet.slug, item.tracker_key)
            charge = {
                "project_id": projet.id,
                "project_slug": projet.slug,
                "work_item_id": item.id,
                "tracker_key": item.tracker_key,
            }
            try:
                await get_temporal().start_interpreter(workflow_id, charge)
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
