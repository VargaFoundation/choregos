# SPDX-License-Identifier: Apache-2.0
"""`make demo` — la chaîne complète sur un poste, sans cluster ni serveur.

Un ticket entre, un agent simulé travaille, les gates vérifient, un humain valide,
le train déploie : c'est le scénario M1 du plan, joué en mémoire avec les fakes.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # les fakes exposent des méthodes de mise en scène (seed, set_diff…)
    from choregos_adapters.fakes import FakeCd, FakeExecutor, FakeGateway, FakeScm, FakeTracker

BANNER = """
  ┌─────────────────────────────────────────────────────────────────────┐
  │  Choregos — offline demonstration                                   │
  │  A work item comes in, a controlled production release comes out.  │
  └─────────────────────────────────────────────────────────────────────┘
"""


async def seed() -> tuple[str, str, Any]:
    """Crée l'organisation, le projet, le ticket et les adaptateurs simulés."""
    from choregos_adapters import AdapterSet
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import create_all, session_scope
    from choregos_api.services import Naissance, ensure_defaults, nouveau_ticket
    from choregos_contracts import Evidence, StageResult, StageStatus
    from choregos_core.domain import WorkItemData

    from .activities.base import set_adapters_override

    await create_all()
    adapters = AdapterSet.fakes()
    tracker: FakeTracker = adapters.tracker  # type: ignore[assignment]
    scm: FakeScm = adapters.scm  # type: ignore[assignment]
    executor: FakeExecutor = adapters.executor  # type: ignore[assignment]
    gateway: FakeGateway = adapters.gateway  # type: ignore[assignment]
    cd: FakeCd = adapters.cd  # type: ignore[assignment]
    tracker.project_slug = "varga/billing-api"
    gateway.auto_usage = True  # la démo montre des coûts crédibles
    set_adapters_override(adapters)

    async with session_scope(orgs="*") as session:
        org = Organization(slug="varga", name="Varga Foundation")
        session.add(org)
        await session.flush()
        project = Project(
            org_id=org.id,
            slug="billing-api",
            name="Billing API",
            status="active",
            config={
                "slug": "billing-api",
                "org": "varga",
                "repo": {
                    "url": "https://github.com/varga/billing-api.git",
                    "default_branch": "main",
                    "language": "python",
                    "test_command": "make test",
                },
                "gitops": {"repo_url": "https://github.com/varga/gitops.git", "apps": ["billing-api"]},
                "notify": {"slack_channel": "#billing"},
            },
        )
        session.add(project)
        await session.flush()
        await ensure_defaults(session, project)
        item = await nouveau_ticket(
            session,
            project,
            Naissance(
                tracker_key="varga/billing-api#123",
                title="Credits are not deducted from the invoice total",
                body="When an order has a credit, the total shown ignores the discount.",
                size="M",
                risk="low",
                allowed_paths=("src/orders/**", "tests/orders/**"),
            ),
        )
        project_id, work_item_id, key = project.id, item.id, item.tracker_key

    tracker.items[key] = WorkItemData(key=key, title=item.title, state="Todo")
    scm.set_diff(
        "varga/billing-api",
        "main",
        "choregos/123",
        [("src/orders/total.py", 24, 6), ("tests/orders/test_total.py", 38, 0)],
    )
    cd.set_health("billing-api", "Healthy")

    def evidence() -> Evidence:
        return Evidence(
            tests_passed=True,
            tests_run=412,
            tests_failed=0,
            coverage_delta=1.2,
            lint="ok",
            typecheck="ok",
            security_scan="ok",
        )

    executor.queue_result(
        StageResult(
            status=StageStatus.DONE,
            summary="specification written: 6 allowed paths, Given/When/Then criteria",
            outputs={
                "size": "M",
                "risk": "low",
                "spec_markdown": "## Spec\n…",
                "allowed_paths": ["src/orders/**", "tests/orders/**"],
            },
            evidence=evidence(),
        )
    )
    executor.queue_result(
        StageResult(
            status=StageStatus.DONE,
            summary="credits deducted from the total; 3 tests added",
            artifacts={"branch": "choregos/123", "commits": ["fix(orders): deduct credits from the total"]},
            evidence=evidence(),
            findings=[
                {
                    "title": "N+1 query when loading the lines",
                    "type": "perf",
                    "severity": "medium",
                    "evidence": "src/orders/repository.py:88",
                    "suggested_fix": "selectinload(Order.lines)",
                    "estimate": "S",
                }
            ],
        )
    )
    executor.queue_result(
        StageResult(status=StageStatus.DONE, summary="412 tests green, coverage +1.2", evidence=evidence())
    )
    return project_id, work_item_id, adapters


async def main_async() -> int:
    from .local import run_local

    print(BANNER)
    project_id, work_item_id, adapters = await seed()

    print("· work item varga/billing-api#123 created, workflow `default-simple` pinned\n")
    seen: dict[str, Any] = {}

    async def world(state: str) -> None:
        """Le monde extérieur réagit : la CI tourne, un mainteneur relit, Argo déploie."""
        if state == "pr_open" and adapters.scm.prs and "pr" not in seen:
            ref = next(iter(adapters.scm.prs.values())).ref
            adapters.scm.set_checks(ref, "success")
            adapters.scm.submit_review(ref, "marie", "approved")
            seen["pr"] = ref
            print(f"· CI green and review approved on {ref.url}")
        if state == "merged" and "merged" not in seen:
            seen["merged"] = True
            print("· the release train takes the work item in its next batch")

    trace = await run_local(project_id, work_item_id, approve=lambda *_: True, on_state=world)
    ref = seen.get("pr")

    print(trace.summary())
    print("\n── Comment written on the work item ──\n")
    print(adapters.tracker.status_comment("varga/billing-api#123") or "(none)")

    findings = [item for item in adapters.tracker.items.values() if "finding" in item.labels]
    print("\n── What the platform produced ──")
    print(f"· runs                 : {len([s for s in trace.steps if s.run_id])}")
    print(f"· total cost           : {trace.cost_usd:.2f} USD")
    print(f"· pull request         : {ref.url if ref else '—'}")
    print(f"· findings filed       : {len(findings)}" + (f" ({findings[0].key})" if findings else ""))
    print(f"· notifications        : {len(adapters.notify.sent)}")
    print(f"· final state          : {trace.final_state}")
    return 0 if trace.final_state in {"merged", "deployed_prod", "pr_open"} else 1


def main() -> None:
    os.environ.setdefault("CHOREGOS_FAKES", "1")
    if "CHOREGOS_DATABASE_URL" not in os.environ:
        path = Path(tempfile.mkdtemp(prefix="choregos-demo-")) / "demo.db"
        os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{path}"
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
