#!/usr/bin/env python3
"""`make dev-seed` : une organisation, un projet, dix tickets, un train.

Le décor est volontairement crédible : des tickets à différents stades, un finding à
trier, un lot en attente de départ. C'est ce qui permet de juger le front d'un coup d'œil.
"""

from __future__ import annotations

import asyncio
import os
import random
from datetime import timedelta

TITLES = [
    ("Les avoirs ne sont pas déduits du total de la facture", "M", "low"),
    ("Exporter les factures en PDF", "L", "low"),
    ("Corriger l'arrondi des remises en cascade", "S", "medium"),
    ("Migrer le module de paiement vers pydantic v2", "L", "medium"),
    ("Ajouter la TVA intracommunautaire", "M", "high"),
    ("Le webhook de relance part deux fois", "S", "medium"),
    ("Page d'historique des factures trop lente", "M", "low"),
    ("Journaliser les échecs de prélèvement", "S", "low"),
    ("Supporter les paiements partiels", "XL", "high"),
    ("Documenter le calcul des pénalités de retard", "S", "low"),
]

STATES = [
    "inbox",
    "awaiting_spec_approval",
    "ready",
    "in_progress",
    "verifying",
    "pr_open",
    "merged",
    "deployed_prod",
    "needs_human",
    "inbox",
]


async def main() -> int:
    os.environ.setdefault("CHOREGOS_FAKES", "1")
    from choregos_api.db.models import (
        CostLedger,
        Finding,
        HumanRequest,
        Organization,
        Project,
        Release,
        Run,
        WorkItem,
    )
    from choregos_api.db.session import create_all, session_scope
    from choregos_api.services import ensure_defaults
    from choregos_core import utcnow
    from sqlalchemy import select

    await create_all()
    random.seed(7)

    async with session_scope() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == "varga"))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(slug="varga", name="Varga Foundation")
            session.add(org)
            await session.flush()

        project = (
            await session.execute(select(Project).where(Project.slug == "billing-api"))
        ).scalar_one_or_none()
        if project is None:
            project = Project(
                org_id=org.id,
                slug="billing-api",
                name="Billing API",
                status="active",
                template_ref="github-tekton-argo-k8s@1.0.0",
                config={
                    "slug": "billing-api",
                    "org": "varga",
                    "repo": {
                        "url": "https://github.com/varga/billing-api.git",
                        "default_branch": "main",
                        "language": "python",
                        "test_command": "make test",
                    },
                    "gitops": {
                        "repo_url": "https://github.com/varga/billing-gitops.git",
                        "apps": ["billing-api"],
                    },
                    "notify": {"slack_channel": "#billing"},
                },
            )
            session.add(project)
            await session.flush()
        await ensure_defaults(session, project)

        existing = (
            (await session.execute(select(WorkItem).where(WorkItem.project_id == project.id))).scalars().all()
        )
        if existing:
            print(f"· le projet contient déjà {len(existing)} tickets — rien à ajouter")
            return 0

        for index, ((title, size, risk), state) in enumerate(zip(TITLES, STATES, strict=True), start=120):
            item = WorkItem(
                project_id=project.id,
                tracker_key=f"varga/billing-api#{index}",
                title=title,
                body_snapshot="Ticket de démonstration créé par `make dev-seed`.",
                url=f"https://github.com/varga/billing-api/issues/{index}",
                size=size,
                risk=risk,
                state=state,
                allowed_paths=["src/orders/**", "tests/orders/**"],
                created_at=utcnow() - timedelta(days=random.randint(1, 20)),
                closed_at=utcnow() if state == "deployed_prod" else None,
                totals={},
            )
            session.add(item)
            await session.flush()

            spent = 0.0
            for role in ("refine", "implement", "verify")[: 1 + (index % 3)]:
                cost = round(random.uniform(0.2, 3.5), 2)
                spent += cost
                run = Run(
                    work_item_id=item.id,
                    project_id=project.id,
                    transition_id=f"t-{role}",
                    stage_role=role,
                    attempt=1,
                    actor=role,
                    backend="openhands",
                    model="anthropic/claude-sonnet-5",
                    executor_kind="tekton",
                    status="succeeded",
                    started_at=utcnow() - timedelta(hours=random.randint(2, 40)),
                    ended_at=utcnow() - timedelta(hours=random.randint(0, 2)),
                    cost_usd=cost,
                    tokens={
                        "tokens_in": random.randint(40_000, 400_000),
                        "tokens_out": random.randint(2_000, 25_000),
                        "tokens_cached": random.randint(10_000, 300_000),
                        "cost_usd": cost,
                        "cost_eur": round(cost * 0.92, 4),
                        "runs": 1,
                    },
                    spend_collected=True,
                    result={
                        "schema": "choregos/StageResult/v1",
                        "status": "done",
                        "summary": f"étape {role} terminée",
                        "evidence": {"tests_passed": True, "tests_run": random.randint(80, 500)},
                    },
                    allowed_paths=["src/orders/**"],
                )
                session.add(run)
                session.add(
                    CostLedger(
                        project_id=project.id,
                        work_item_id=item.id,
                        provider="anthropic",
                        model="platform/standard",
                        backend="openhands",
                        stage_role=role,
                        size=size,
                        tokens_in=120_000,
                        tokens_out=9_000,
                        tokens_cached=60_000,
                        cost_usd=cost,
                        cost_eur=round(cost * 0.92, 4),
                        fx_rate=0.92,
                        ts=utcnow() - timedelta(days=random.randint(0, 13)),
                    )
                )
            item.totals = {
                "tokens_in": 360_000,
                "tokens_out": 27_000,
                "tokens_cached": 180_000,
                "cost_usd": round(spent, 2),
                "cost_eur": round(spent * 0.92, 2),
                "runs": 3,
                "duration_s": 5400,
            }
            if state == "awaiting_spec_approval":
                session.add(
                    HumanRequest(
                        work_item_id=item.id,
                        project_id=project.id,
                        transition_id="t-approve-spec",
                        kind="approval",
                        payload={"summary": "Validation de la spécification demandée"},
                        requested_at=utcnow() - timedelta(hours=3),
                        due_at=utcnow() + timedelta(hours=21),
                    )
                )
            if state == "pr_open":
                item.pr_url = f"https://github.com/varga/billing-api/pull/{index + 300}"

        session.add(
            Finding(
                project_id=project.id,
                title="Requête N+1 sur le chargement des lignes de commande",
                type="perf",
                severity="medium",
                evidence="src/orders/repository.py:88 — 1 + N requêtes pour 200 lignes",
                suggested_fix="selectinload(Order.lines) dans OrderRepository.get",
                estimate="S",
                status="pending",
            )
        )
        session.add(
            Release(
                project_id=project.id,
                env="prod",
                batch_no=41,
                status="collecting",
                items=[{"work_item_key": "varga/billing-api#125", "sha": "a1b2c3", "title": TITLES[5][0]}],
                started_at=utcnow() - timedelta(minutes=20),
            )
        )

    print("✓ seed : organisation `varga`, projet `billing-api`, 10 tickets, 1 finding, 1 lot en collecte")
    print("  front : http://localhost:3000/p/billing-api/board")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
