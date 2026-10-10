"""`approvals.*` et `review.require_human_for_risk` ne gardent rien : la plateforme le dit (ADR 0044).

Finding #286 : les presets les remplissaient, personne ne les lisait. Le contrat ne change pas —
une politique qui les porte s'enregistre toujours —, mais l'enregistrement le journalise, clé par
clé, avec ce qu'il faut faire à la place : déclarer l'humain dans le workflow.
"""

from __future__ import annotations

from typing import Any

import structlog
from httpx import AsyncClient

POLITIQUE_QUI_PROMET = """
apiVersion: choregos/v1
kind: Policy
metadata: { name: promesses, version: 1 }
approvals:
  merge: { required: always, group: maintainers }
  spec: { required: never }
review: { require_human_for_risk: [high] }
"""


def _non_appliquees(evenements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in evenements if e.get("event") == "policy.not_enforced"]


async def test_enregistrer_une_politique_qui_promet_une_garde_le_journalise(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    with structlog.testing.capture_logs() as evenements:
        reponse = await client.put(
            f"/api/v1/projects/{project['id']}/policy", json={"yaml": POLITIQUE_QUI_PROMET}
        )
    assert reponse.status_code == 200, reponse.text  # toujours valide : le contrat n'a pas bougé
    signales = _non_appliquees(evenements)
    assert [e["path"] for e in signales] == ["approvals.merge", "review.require_human_for_risk"]
    for evenement in signales:
        assert evenement["log_level"] == "warning"
        assert evenement["project"] == project["slug"]
        assert evenement["policy"] == "promesses"
        assert "Declare a human transition in the workflow" in evenement["message"]


async def test_un_projet_neuf_sur_le_preset_ne_signale_rien(client: AsyncClient, admin: str) -> None:
    """Le preset `solo` (projet sans gabarit) écrivait `approvals.spec|prod` et une relecture
    humaine : il ne promet plus rien, et sa politique ne s'avertit pas à la naissance du projet."""
    with structlog.testing.capture_logs() as evenements:
        reponse = await client.post(
            "/api/v1/orgs/varga/projects",
            json={
                "slug": "sans-promesse",
                "name": "Sans promesse",
                "config": {"slug": "sans-promesse", "org": "varga"},
            },
        )
        assert reponse.status_code == 201, reponse.text
        politique = await client.get(f"/api/v1/projects/{reponse.json()['id']}/policy")
    assert politique.json()["name"] == "solo"
    assert _non_appliquees(evenements) == []
