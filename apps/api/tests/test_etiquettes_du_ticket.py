"""Le ticket garde ses étiquettes, et les porte au train (#279, S22-17).

Elles servaient au routage (ADR 0031) puis se perdaient : `signal_train` embarquait avec
`labels: []`, et un ticket étiqueté `hotfix` ne prenait jamais la voie express quand c'était
l'interpréteur qui l'embarquait. Elles vivent dans `documents.labels` : ni le contrat ni le schéma
de la base ne changent.
"""

from __future__ import annotations

import json
from typing import Any

from httpx import AsyncClient

DEPOT = {"full_name": "varga/billing-api"}
QUI = {"login": "a"}


async def _poster(client: AsyncClient, evenement: str, livraison: str, corps: dict[str, Any]) -> None:
    reponse = await client.post(
        "/api/v1/webhooks/github",
        content=json.dumps(corps).encode(),
        headers={
            "X-GitHub-Event": evenement,
            "X-GitHub-Delivery": livraison,
            "content-type": "application/json",
        },
    )
    assert reponse.status_code == 202, reponse.text


async def _etiquetage(
    client: AsyncClient, livraison: str, action: str, etiquette: str, courantes: list[str]
) -> None:
    """Ce que GitHub envoie : l'étiquette posée ou retirée, et la liste COURANTE du ticket."""
    issue = {"number": 7, "title": "Panne des avoirs", "labels": [{"name": n} for n in courantes]}
    corps = {
        "action": action,
        "repository": DEPOT,
        "issue": issue,
        "label": {"name": etiquette},
        "sender": QUI,
    }
    await _poster(client, "issues", livraison, corps)


async def _etiquettes() -> list[str]:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        item = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == "varga/billing-api#7"))
        ).scalar_one()
        return list((item.documents or {}).get("labels") or [])


async def test_le_ticket_garde_ses_etiquettes_de_sa_naissance_et_les_suit(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    # Né d'une ouverture (`tracker.item.created`) : seule la naissance range ses étiquettes.
    issue = {
        "number": 7,
        "title": "Panne des avoirs",
        "labels": [{"name": "agent-ready"}, {"name": "hotfix"}],
    }
    await _poster(
        client, "issues", "e0", {"action": "opened", "repository": DEPOT, "issue": issue, "sender": QUI}
    )
    assert await _etiquettes() == ["agent-ready", "hotfix"], "les étiquettes de sa naissance"

    await _etiquetage(client, "e2", "unlabeled", "hotfix", ["agent-ready"])
    assert await _etiquettes() == ["agent-ready"], "une étiquette retirée s'en va"

    await _etiquetage(client, "e3", "labeled", "hotfix", ["agent-ready", "hotfix"])
    assert await _etiquettes() == ["agent-ready", "hotfix"], "une étiquette posée après la naissance"


async def test_le_webhook_embarque_le_ticket_avec_ses_etiquettes(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """La PR ne porte pas `hotfix` ; le ticket, si : il prend la voie express."""
    from choregos_api.temporal import get_temporal

    await _etiquetage(client, "e1", "labeled", "agent-ready", ["agent-ready", "hotfix"])
    pr = {
        "number": 107,
        "merged": True,
        "html_url": "https://github.com/varga/billing-api/pull/107",
        "head": {"ref": "choregos/7-avoirs", "sha": "sha7"},
        "base": {"ref": "main"},
        "labels": [{"name": "backend"}],
    }
    await _poster(
        client,
        "pull_request",
        "fusion-7",
        {"action": "closed", "repository": DEPOT, "pull_request": pr, "sender": QUI},
    )
    (embarquement,) = [
        charge
        for train, nom, charge in get_temporal().signals  # type: ignore[attr-defined]
        if train == "train-billing-api-prod" and nom == "merged"
    ]
    assert embarquement["labels"] == ["backend", "agent-ready", "hotfix"]
