# SPDX-License-Identifier: Apache-2.0
"""The trial's scenery, laid INSIDE the API container as the inventory and the orchestrator would.

Hosts and services in the database (no inventory synchronisation exists yet), a work item, its
run, and that run's token - minted with the stack's shared key, as the orchestrator does.

    docker compose exec -T -e PROJECT=... -e SLUG=... -e RUN=... api python - < scenery.py
"""

from __future__ import annotations

import asyncio
import json
import os


async def main() -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from choregos_ontology.service.store import ManagedObject

    project, slug, run_id = os.environ["PROJECT"], os.environ["SLUG"], os.environ["RUN"]
    objects = [
        ("host", "node-1", {"id": "node-1", "name": "node-1", "os": "ubuntu", "ip": "10.0.0.11"}),
        ("host", "node-2", {"id": "node-2", "name": "node-2", "os": "debian", "ip": "10.0.0.12"}),
        ("service", "svc-a", {"id": "svc-a", "name": "api", "host_id": "node-1"}),
        ("service", "svc-b", {"id": "svc-b", "name": "db", "host_id": "node-2"}),
    ]
    key = f"{slug}#1"
    async with session_scope(orgs="*") as session:
        for type_, ident, properties in objects:
            session.add(ManagedObject(project_id=project, object_type=type_, id=ident, properties=properties))
        item = WorkItem(project_id=project, tracker_key=key, title="reboot-required", state="ready")
        session.add(item)
        await session.flush()
        session.add(Run(id=run_id, work_item_id=item.id, project_id=project, stage_role="infra_analysis"))
    print(json.dumps({"token": mint_run_token(run_id, project_slug=slug, work_item_key=key, ttl_minutes=60)}))


asyncio.run(main())
