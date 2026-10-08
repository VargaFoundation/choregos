"""Le gabarit `github-software-delivery` (S21-23) : trois workflows, leur routage, sa politique, et les
agents du catalogue qu'ils nomment — installés dans l'organisation à la naissance du projet."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

AGENTS = {
    "triager",
    "spec-writer",
    "planner",
    "developer",
    "tester",
    "reviewer",
    "security-reviewer",
    "review-responder",
    "ci-fixer",
    "release-notes-writer",
    "prod-verifier",
    "researcher",
    "architect",
}


async def _projet(client: AsyncClient) -> dict[str, Any]:
    cree = await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": "dev",
            "name": "Development",
            "template_ref": "github-software-delivery@1.0.0",
            "config": {
                "slug": "dev",
                "org": "varga",
                "repo": {"url": "https://github.com/varga/sandbox.git", "default_branch": "main"},
            },
        },
    )
    assert cree.status_code == 201, cree.text
    return dict(cree.json())


async def test_un_projet_ne_du_gabarit_recoit_ses_trois_workflows_sa_politique_et_ses_agents(
    client: AsyncClient, admin: str
) -> None:
    pid = (await _projet(client))["id"]
    actifs = {w["name"] for w in (await client.get(f"/api/v1/projects/{pid}/workflows")).json()}
    assert actifs == {"dev-simple", "study", "dev-complex"}
    assert (await client.get(f"/api/v1/projects/{pid}/policy")).json()["name"] == "software-delivery"

    agents = {a["slug"] for a in (await client.get("/api/v1/orgs/varga/agents")).json()}
    assert agents >= AGENTS, sorted(AGENTS - agents)
    skills = {s["slug"] for s in (await client.get("/api/v1/orgs/varga/skills")).json()}
    assert "madr-4" in skills

    # Les checks de la PR se lisent par le dépôt : rien ne réclame un Tekton à un projet GitHub Actions.
    exigences = [r["capability"] for r in (await client.get(f"/api/v1/projects/{pid}/requirements")).json()]
    assert {"tracker", "scm", "cd", "runtime", "gateway"} <= set(exigences)
    assert "ci" not in exigences


async def test_les_etiquettes_routent_vers_le_bon_workflow(client: AsyncClient, admin: str) -> None:
    pid = (await _projet(client))["id"]
    assert (
        await client.put(
            f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "internal", "config": {}}
        )
    ).status_code == 200
    for etiquettes, attendu in (
        ([], "dev-simple"),
        (["bug"], "dev-simple"),
        (["adr"], "study"),
        (["spike", "bug"], "study"),
        (["bug", "complex"], "dev-complex"),
        (["feature"], "dev-complex"),
    ):
        ticket = await client.post(
            f"/api/v1/projects/{pid}/work-items", json={"title": "x", "labels": etiquettes, "start": False}
        )
        assert ticket.status_code == 201, ticket.text
        assert (ticket.json()["workflow_name"], ticket.json()["state"]) == (attendu, "inbox"), etiquettes


def test_chaque_workflow_prend_le_train_qu_il_annonce_apres_la_fusion() -> None:
    """Ce que le webhook de GitHub embarque (ADR 0041) : la prod sans approbation pour un correctif,
    staging d'abord pour une fonctionnalité, rien pour une étude."""
    from pathlib import Path

    from choregos_core import parse_workflow
    from choregos_core.dsl.trains import approbation_du_train, env_du_train, train_apres_fusion

    dossier = Path(__file__).resolve().parents[3] / "templates" / "github-software-delivery" / "workflows"
    attendus = {
        "dev-simple": ("prod", {"required": False, "group": None}),
        "dev-complex": ("staging", {"required": False, "group": None}),
        "study": None,
    }
    for nom, attendu in attendus.items():
        workflow = parse_workflow((dossier / f"{nom}.yaml").read_text(encoding="utf-8"))[0]
        train = train_apres_fusion(workflow)
        trouve = None if train is None else (env_du_train(train), dict(approbation_du_train(workflow, train)))
        assert trouve == attendu, nom
