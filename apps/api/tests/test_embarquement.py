"""Le webhook de GitHub embarque un ticket fusionné dans le train de SON workflow (ADR 0041, S21-21).

Il embarquait tout ticket fusionné dans le train de prod : une étude qui fusionne un ADR partait en
production, une livraison prudente sautait son staging, et l'approbation que le workflow exige
n'arrivait jamais jusqu'au train.
"""

from __future__ import annotations

import json
from typing import Any

from choregos_core.dsl import template_yaml
from httpx import AsyncClient

ETUDE = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: study, version: 1 }
actors:
  architect: { type: agent, role: architect, model: "profile:standard" }
  platform: { type: system }
states:
  inbox: { display: Inbox, kind: wait }
  written: { display: ADR written }
  pr_open: { display: PR open }
  merged: { display: Merged, terminal: true }
transitions:
  - { id: t-write, from: inbox, to: written, by: architect, outputs: [adr_markdown] }
  - { id: t-pr, from: written, to: pr_open, by: platform, does: open_pr }
  - { id: t-merge, from: pr_open, to: merged, by: platform, does: merge_pr, gates: [ci_green] }
"""


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


async def _ticket_fusionne(client: AsyncClient, numero: int, etiquettes: list[str]) -> None:
    depot = {"full_name": "varga/billing-api"}
    issue = {"number": numero, "title": f"ticket {numero}", "labels": [{"name": n} for n in etiquettes]}
    qui = {"login": "a"}
    etiquetage = {"action": "labeled", "repository": depot, "issue": issue, "label": {"name": "agent-ready"}}
    await _poster(client, "issues", f"ne-{numero}", {**etiquetage, "sender": qui})
    pr = {
        "number": 100 + numero,
        "merged": True,
        "html_url": f"https://github.com/varga/billing-api/pull/{numero}",
        "head": {"ref": f"choregos/{numero}-x", "sha": f"sha{numero}"},
        "base": {"ref": "main"},
        "labels": [],
    }
    fusion = {"action": "closed", "repository": depot, "pull_request": pr, "sender": qui}
    await _poster(client, "pull_request", f"fusion-{numero}", fusion)


def _embarquements() -> dict[str, list[dict[str, Any]]]:
    from choregos_api.temporal import get_temporal

    trains: dict[str, list[dict[str, Any]]] = {}
    for workflow_id, nom, charge in get_temporal().signals:  # type: ignore[attr-defined]
        if nom == "merged":
            trains.setdefault(workflow_id, []).append(charge)
    return trains


async def test_chaque_ticket_fusionne_monte_dans_le_train_de_son_workflow(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    prudente = template_yaml("advanced").replace("name: advanced", "name: careful", 1)
    for nom, texte in (("study", ETUDE), ("careful", prudente)):
        publie = await client.put(f"/api/v1/projects/{pid}/workflows/{nom}", json={"yaml": texte})
        assert publie.status_code in {200, 201}, publie.text
    routage = {
        "default": "default-simple",
        "rules": [
            {"when": {"labels_any": ["adr"]}, "workflow": "study"},
            {"when": {"labels_any": ["careful"]}, "workflow": "careful"},
        ],
    }
    assert (await client.put(f"/api/v1/projects/{pid}/workflow-routing", json=routage)).status_code == 200

    await _ticket_fusionne(client, 1, ["agent-ready"])
    await _ticket_fusionne(client, 2, ["agent-ready", "adr"])
    await _ticket_fusionne(client, 3, ["agent-ready", "careful"])

    trains = _embarquements()
    assert set(trains) == {"train-billing-api-prod", "train-billing-api-staging"}, trains
    (prod,) = trains["train-billing-api-prod"]
    assert prod["work_item_key"] == "varga/billing-api#1"
    assert prod["approval"] == {"required": True, "group": "release-captains"}, (
        "le capitaine de default-simple"
    )
    (staging,) = trains["train-billing-api-staging"]
    assert staging["work_item_key"] == "varga/billing-api#3", (
        "la livraison prudente passe d'abord par staging"
    )
    assert staging["approval"] == {"required": False, "group": None}
    assert all(c["work_item_key"] != "varga/billing-api#2" for cs in trains.values() for c in cs), (
        "une étude qui fusionne un ADR ne part nulle part"
    )
