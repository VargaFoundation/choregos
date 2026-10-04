# SPDX-License-Identifier: Apache-2.0
"""Le décor de l'essai, posé DANS le conteneur de l'API comme le feraient l'inventaire et l'orchestrateur.

Des hôtes et des services en base (aucune synchronisation d'inventaire n'existe encore), un ticket, son
run, et le jeton de ce run — frappé avec la clé partagée de la pile, comme le fait l'orchestrateur.

    docker compose exec -T -e PROJET=… -e SLUG=… -e RUN=… api python - < decor.py
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

    projet, slug, run_id = os.environ["PROJET"], os.environ["SLUG"], os.environ["RUN"]
    objets = [
        ("host", "node-1", {"id": "node-1", "name": "node-1", "os": "ubuntu", "ip": "10.0.0.11"}),
        ("host", "node-2", {"id": "node-2", "name": "node-2", "os": "debian", "ip": "10.0.0.12"}),
        ("service", "svc-a", {"id": "svc-a", "name": "api", "host_id": "node-1"}),
        ("service", "svc-b", {"id": "svc-b", "name": "db", "host_id": "node-2"}),
    ]
    cle = f"{slug}#1"
    async with session_scope(orgs="*") as session:
        for type_, ident, proprietes in objets:
            session.add(ManagedObject(project_id=projet, object_type=type_, id=ident, properties=proprietes))
        item = WorkItem(project_id=projet, tracker_key=cle, title="reboot-required", state="ready")
        session.add(item)
        await session.flush()
        session.add(Run(id=run_id, work_item_id=item.id, project_id=projet, stage_role="analyse_infra"))
    print(json.dumps({"token": mint_run_token(run_id, project_slug=slug, work_item_key=cle, ttl_minutes=60)}))


asyncio.run(main())
