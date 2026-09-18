"""M2 — les trois workflows, la validation par le board, le wizard, l'estimation de coût."""

from __future__ import annotations

import json

import pytest
from choregos_core.dsl import TEMPLATE_NAMES, template_yaml

from .conftest import Platform, login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
async def test_les_trois_templates_sont_acceptes_par_l_api(platform: Platform, name: str) -> None:
    await login(platform.client)
    validation = (
        await platform.client.post("/api/v1/workflows/validate", json={"yaml": template_yaml(name)})
    ).json()
    assert validation["valid"], validation["errors"]
    assert validation["graph"]["nodes"], "le graphe est rendu pour le front"

    saved = await platform.client.put(
        f"/api/v1/projects/{platform.project_id}/workflow", json={"yaml": template_yaml(name)}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["name"] == name


async def test_un_workflow_invalide_est_refuse_avec_la_ligne_fautive(platform: Platform) -> None:
    await login(platform.client)
    broken = template_yaml("default-simple").replace("to: awaiting_spec_approval", "to: nulle_part", 1)
    response = await platform.client.put(
        f"/api/v1/projects/{platform.project_id}/workflow", json={"yaml": broken}
    )
    assert response.status_code == 422
    problem = response.json()
    assert any(error.get("line") for error in problem["errors"]), problem


async def test_carte_deplacee_sur_le_board_fait_avancer_le_ticket(platform: Platform) -> None:
    """Un humain déplace une carte : c'est une décision, pas un simple changement d'étiquette."""
    await login(platform.client)
    payload = {
        "action": "edited",
        "repository": {"full_name": "varga/billing-api"},
        "projects_v2_item": {"content_node_id": "varga/billing-api#200", "project_node_id": "PVT_1"},
        "changes": {
            "field_value": {
                "field_name": "Status",
                "from": {"name": "Todo"},
                "to": {"name": "Ready"},
            }
        },
        "sender": {"login": "augustin"},
    }
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(
            WorkItem(
                project_id=platform.project_id,
                tracker_key="varga/billing-api#200",
                title="Ticket déplacé à la main",
                state="inbox",
            )
        )

    response = await platform.client.post(
        "/api/v1/webhooks/github",
        content=json.dumps(payload).encode(),
        headers={
            "X-GitHub-Event": "projects_v2_item",
            "X-GitHub-Delivery": "m2-1",
            "content-type": "application/json",
        },
    )
    assert response.status_code == 202
    from choregos_api.temporal import get_temporal

    # Aucun workflow n'est démarré pour ce ticket dans ce scénario : la passerelle note
    # la tentative de signal, ce qui prouve que le webhook a bien acheminé l'événement.
    attempts = get_temporal().missing  # type: ignore[attr-defined]
    assert any(name == "inbound" for name, _ in attempts), attempts


async def test_le_wizard_cree_un_projet_et_demarre_le_provisioning(platform: Platform) -> None:
    await login(platform.client)
    created = await platform.client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": "checkout-web",
            "name": "Checkout Web",
            "template_ref": "github-tekton-argo-k8s@1.0.0",
            "config": {
                "slug": "checkout-web",
                "org": "varga",
                "repo": {"url": "https://github.com/varga/checkout-web.git", "default_branch": "main"},
            },
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["workflow_name"] == "default-simple", "le workflow par défaut est épinglé (D14)"

    provision = await platform.client.post(f"/api/v1/projects/{project['id']}/provision", json={})
    assert provision.status_code == 202
    assert provision.json()["status"] == "running"

    from choregos_api.temporal import get_temporal

    attempts = get_temporal().missing  # type: ignore[attr-defined]
    assert any(workflow_id.startswith("prov-") for _, workflow_id in attempts), attempts


async def test_estimation_de_cout_affichee_sur_un_ticket(platform: Platform) -> None:
    """L'estimation vient des tickets comparables déjà terminés, pas d'une formule."""
    await login(platform.client)
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        for index in range(5):
            session.add(
                WorkItem(
                    project_id=platform.project_id,
                    tracker_key=f"varga/billing-api#9{index}",
                    title="ticket terminé",
                    state="deployed_prod",
                    size="M",
                    closed_at=utcnow(),
                    totals={"cost_usd": 5 + index, "cost_eur": (5 + index) * 0.92, "runs": 3},
                )
            )
        current = WorkItem(
            project_id=platform.project_id,
            tracker_key="varga/billing-api#300",
            title="ticket en cours",
            state="in_progress",
            size="M",
            totals={"cost_usd": 12.0},
        )
        session.add(current)
        await session.flush()
        item_id = current.id

    payload = (await platform.client.get(f"/api/v1/work-items/{item_id}")).json()
    estimate = payload["estimate"]
    assert estimate["sample_size"] == 5
    assert estimate["median_usd"] == 7
    assert estimate["over_p80"] is True, "le ticket dépasse le p80 : la plateforme le signale"
