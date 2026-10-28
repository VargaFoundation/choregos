"""The agent catalogue, seen from an organisation (ADR 0040, S21-17).

Installing never overwrites; an upgrade publishes the next version only when the catalogue changed; an
organisation's own agent of the same name is never touched; a workflow or a template that names a
catalogue agent installs it. Since S43-09 the catalogue is the offline plugins' (ADR 0093 §6): what it
installs carries the plugin's provenance, `plugin:<plugin>/<slug>@<version>`.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

import pytest
from choregos_api.schemas import AgentCreate
from choregos_api.services.agents import erreurs_d_une_version
from choregos_core.catalogue_d_agents import entree, entrees
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"


def _replace_entry(monkeypatch: pytest.MonkeyPatch, nouveau: Any) -> None:
    """The offline catalogue with `nouveau` in place of the entry of the same slug."""
    from choregos_api.services import catalogue_d_agents as service

    base = service.offline_catalogue()
    entries = (*(e for e in base.entries if e.slug != nouveau.slug), nouveau)
    view = dataclasses.replace(base, entries=entries)
    monkeypatch.setattr(service, "offline_catalogue", lambda: view)


def test_chaque_entree_du_catalogue_est_un_agent_installable() -> None:
    """Le document de chaque entrée passe `AgentCreate` et ses instructions se rendent : une entrée
    cassée se verrait ici, pas chez la première organisation qui l'installe."""
    for proposition in entrees():
        corps = AgentCreate.model_validate(proposition.document)
        erreurs_d_une_version(corps.spec)


async def test_le_catalogue_liste_les_agents_et_dit_s_ils_sont_installes(
    client: AsyncClient, admin: str
) -> None:
    lus = (await client.get(f"{ORG}/agent-catalogue")).json()
    par_slug = {e["slug"]: e for e in lus}
    assert {"developer", "tester", "architect", "claude-code"} <= set(par_slug)
    assert par_slug["developer"]["role"] == "implement" and par_slug["developer"]["installed"] is False
    assert par_slug["chatgpt"]["reach"] == "cloud" and par_slug["claude-code"]["kind"] == "external"
    assert (await client.post(f"{ORG}/agent-catalogue/developer/install")).status_code == 201
    developer = next(
        e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "developer"
    )
    assert (developer["installed"], developer["installed_version"], developer["update_available"]) == (
        True,
        1,
        False,
    )


async def test_installer_un_agent_installe_ses_skills_d_abord(client: AsyncClient, admin: str) -> None:
    installe = await client.post(f"{ORG}/agent-catalogue/architect/install")
    assert installe.status_code == 201, installe.text
    agent = installe.json()
    assert agent["versions"][0]["created_by"] == "plugin:software-delivery/architect@2"
    assert agent["versions"][0]["spec"]["skills"] == [{"slug": "madr-4", "version": None}]
    skills = {s["slug"]: s for s in (await client.get(f"{ORG}/skills")).json()}
    assert skills["madr-4"]["latest_version"] == 1


async def test_reinstaller_ne_reecrit_pas_un_agent_existant(client: AsyncClient, admin: str) -> None:
    assert (await client.post(f"{ORG}/agent-catalogue/tester/install")).status_code == 201
    encore = await client.post(f"{ORG}/agent-catalogue/tester/install")
    assert encore.status_code == 409 and "upgrade" in encore.text


async def test_une_mise_a_jour_publie_la_version_suivante_seulement_si_elle_change(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.post(f"{ORG}/agent-catalogue/triager/install")).status_code == 201
    pareil = await client.post(f"{ORG}/agent-catalogue/triager/install", json={"upgrade": True})
    assert pareil.status_code == 200 and pareil.json()["latest_version"] == 1, (
        "rien n'a changé : rien de publié"
    )

    ancien = entree("triager")
    assert ancien is not None
    document = {**ancien.document, "spec": {**ancien.document["spec"], "instructions": "Triage it, briefly."}}
    nouveau = dataclasses.replace(ancien, version=2, document=document)
    _replace_entry(monkeypatch, nouveau)
    listee = next(e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "triager")
    assert listee["update_available"] is True
    publie = await client.post(f"{ORG}/agent-catalogue/triager/install", json={"upgrade": True})
    assert publie.status_code == 200, publie.text
    versions = publie.json()["versions"]
    assert [v["version"] for v in versions] == [2, 1], "la version 1 ne se réécrit pas"
    assert versions[0]["spec"]["instructions"] == "Triage it, briefly."


async def test_un_agent_de_l_organisation_du_meme_nom_n_est_jamais_touche(
    client: AsyncClient, admin: str
) -> None:
    propre = await client.post(f"{ORG}/agents", json={"slug": "developer", "display_name": "Our developer"})
    assert propre.status_code == 201
    assert (
        await client.post(f"{ORG}/agent-catalogue/developer/install", json={"upgrade": True})
    ).status_code == 409
    listee = next(e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "developer")
    assert (listee["own_agent"], listee["update_available"]) == (True, False)
    agent = (await client.get(f"{ORG}/agents/developer")).json()
    assert agent["display_name"] == "Our developer" and agent["latest_version"] == 1


async def test_seul_un_administrateur_installe(client: AsyncClient, admin: str) -> None:
    from .conftest import login

    membre = await client.post(f"{ORG}/members", json={"email": "dev@varga.dev", "role": "developer"})
    assert membre.status_code in {200, 201}
    await login(client, "dev@varga.dev")
    assert (await client.get(f"{ORG}/agent-catalogue")).status_code == 200, "un membre lit le catalogue"
    assert (await client.post(f"{ORG}/agent-catalogue/developer/install")).status_code == 403
    assert (await client.post(f"{ORG}/agent-catalogue/nobody/install")).status_code in {403, 404}


async def test_un_agent_inconnu_du_catalogue_rend_404(client: AsyncClient, admin: str) -> None:
    assert (await client.post(f"{ORG}/agent-catalogue/nobody/install")).status_code == 404


async def test_publier_un_workflow_qui_nomme_un_agent_du_catalogue_l_installe(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_core.dsl import template_yaml

    yaml = template_yaml("default-simple").replace(
        'dev: { type: agent, role: implement, model: "profile:by_size" }',
        'dev: { type: agent, role: implement, model: "profile:by_size", agent: developer }',
    )
    assert "agent: developer" in yaml
    publie = await client.put(
        f"/api/v1/projects/{project['id']}/workflows/default-simple", json={"yaml": yaml}
    )
    assert publie.status_code == 200, publie.text
    agent = (await client.get(f"{ORG}/agents/developer")).json()
    assert agent["versions"][0]["created_by"] == "plugin:software-delivery/developer@1"


@pytest.fixture
def gabarits(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Un gabarit qui nomme un agent et une skill du catalogue de la plateforme."""
    racine = tmp_path / "gabarits"
    dossier = racine / "essai-catalogue"
    dossier.mkdir(parents=True)
    (dossier / "manifest.yaml").write_text(
        "apiVersion: choregos/v1\nkind: Template\nmetadata: {name: essai-catalogue, version: 1.0.0}\n"
        "defaults:\n  workflows: ['template:default-simple@1']\n"
        "  skills: [catalogue:madr-4]\n  agents: [catalogue:reviewer, catalogue:nobody-knows]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", str(racine))
    return dossier


async def test_un_gabarit_peut_nommer_un_agent_du_catalogue(
    client: AsyncClient, admin: str, gabarits: pathlib.Path
) -> None:
    corps = {
        "slug": "rh",
        "name": "rh",
        "template_ref": "essai-catalogue",
        "config": {"slug": "rh", "org": "varga"},
    }
    refuse = await client.post(f"{ORG}/projects", json=corps)
    assert refuse.status_code == 422 and "catalogue:nobody-knows" in refuse.text, "un nom inconnu est refusé"
    (gabarits / "manifest.yaml").write_text(
        (gabarits / "manifest.yaml").read_text(encoding="utf-8").replace(", catalogue:nobody-knows", ""),
        encoding="utf-8",
    )
    ne = await client.post(f"{ORG}/projects", json=corps)
    assert ne.status_code == 201, ne.text
    reviewer = (await client.get(f"{ORG}/agents/reviewer")).json()
    assert reviewer["versions"][0]["spec"]["skills"] == [{"slug": "madr-4", "version": None}]
    assert "madr-4" in {s["slug"] for s in (await client.get(f"{ORG}/skills")).json()}


# ───────────────────────── les clients externes en un clic (S21-18) ─────────────────────────


async def test_connecter_claude_code_cree_l_agent_frappe_un_jeton_mcp_et_le_rattache(
    client: AsyncClient, admin: str
) -> None:
    connecte = await client.post(f"{ORG}/agent-catalogue/claude-code/connect")
    assert connecte.status_code == 201, connecte.text
    corps = connecte.json()
    assert (corps["agent"]["slug"], corps["agent"]["kind"]) == ("claude-code", "external")
    assert corps["token"]["scopes"] == ["mcp:write"] and corps["token"]["token"], "le jeton, rendu une fois"
    assert corps["oauth_client_id"] is None
    rattaches = (await client.get(f"{ORG}/agents/claude-code/credentials")).json()
    assert [r["token_name"] for r in rattaches] == ["Claude Code"]
    jetons = (await client.get("/api/v1/me/tokens")).json()
    assert "token" not in jetons[0], "relu, le jeton ne se montre plus"


async def test_un_membre_rejoint_l_agent_d_un_client_mais_ne_le_cree_pas(
    client: AsyncClient, admin: str
) -> None:
    from .conftest import login

    membre = await client.post(f"{ORG}/members", json={"email": "dev@varga.dev", "role": "developer"})
    assert membre.status_code in {200, 201}
    await login(client, "dev@varga.dev")
    assert (await client.post(f"{ORG}/agent-catalogue/cursor/connect")).status_code == 403, (
        "créer : un administrateur"
    )
    await login(client, "admin@varga.dev")
    assert (await client.post(f"{ORG}/agent-catalogue/cursor/connect")).status_code == 201
    await login(client, "dev@varga.dev")
    rejoint = await client.post(f"{ORG}/agent-catalogue/cursor/connect", json={"read_only": True})
    assert rejoint.status_code == 201, rejoint.text
    assert rejoint.json()["token"]["scopes"] == ["mcp:read"]
    await login(client, "admin@varga.dev")
    assert len((await client.get(f"{ORG}/agents/cursor/credentials")).json()) == 2, "un jeton par personne"


async def test_un_agent_interne_ne_se_connecte_pas(client: AsyncClient, admin: str) -> None:
    assert (await client.post(f"{ORG}/agent-catalogue/developer/connect")).status_code == 404


async def test_un_client_qui_appelle_depuis_le_cloud_n_est_pas_propose_sans_oauth_ni_https(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from choregos_api.config import reset_settings_cache

    chatgpt = next(e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "chatgpt")
    assert chatgpt["offered"] is False and "OAuth" in chatgpt["unavailable_reason"]
    refuse = await client.post(f"{ORG}/agent-catalogue/chatgpt/connect")
    assert refuse.status_code == 409 and "not offered" in refuse.text

    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_ISSUER", "https://sso.example/realms/varga")
    monkeypatch.setenv(
        "CHOREGOS_MCP_OAUTH_CLIENTS", json.dumps({"chatgpt": {"client_id": "choregos-chatgpt"}})
    )
    reset_settings_cache()
    toujours = next(e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "chatgpt")
    assert toujours["offered"] is False and "https" in toujours["unavailable_reason"], "OAuth, mais en http"

    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", "https://choregos.example")
    reset_settings_cache()
    claude_ai = next(
        e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "claude-ai"
    )
    assert claude_ai["offered"] is False and "claude-ai" in claude_ai["unavailable_reason"], (
        "aucun client pour lui"
    )
    connecte = await client.post(f"{ORG}/agent-catalogue/chatgpt/connect")
    assert connecte.status_code == 201, connecte.text
    assert (connecte.json()["token"], connecte.json()["oauth_client_id"]) == (None, "choregos-chatgpt")
    rattache = (await client.get(f"{ORG}/agents/chatgpt/credentials")).json()
    assert [(r["kind"], r["client_id"]) for r in rattache] == [("oauth_client", "choregos-chatgpt")]


# ──── a catalogue agent installed by a template upgrades like a direct one (S39-25, #674) ────


def _le_catalogue_passe_a_la_version_deux(monkeypatch: pytest.MonkeyPatch, slug: str) -> None:
    ancien = entree(slug)
    assert ancien is not None
    document = {**ancien.document, "spec": {**ancien.document["spec"], "instructions": "Review it, v2."}}
    _replace_entry(monkeypatch, dataclasses.replace(ancien, version=2, document=document))


async def _projet_du_gabarit(client: AsyncClient, gabarits: pathlib.Path) -> None:
    (gabarits / "manifest.yaml").write_text(
        (gabarits / "manifest.yaml").read_text(encoding="utf-8").replace(", catalogue:nobody-knows", ""),
        encoding="utf-8",
    )
    corps = {
        "slug": "rh",
        "name": "rh",
        "template_ref": "essai-catalogue",
        "config": {"slug": "rh", "org": "varga"},
    }
    assert (await client.post(f"{ORG}/projects", json=corps)).status_code == 201


async def test_un_agent_du_catalogue_installe_par_un_gabarit_porte_la_meme_provenance_qu_en_direct(
    client: AsyncClient, admin: str, gabarits: pathlib.Path
) -> None:
    await _projet_du_gabarit(client, gabarits)
    agent = (await client.get(f"{ORG}/agents/reviewer")).json()
    assert agent["versions"][0]["created_by"] == "plugin:software-delivery/reviewer@1"
    assert agent["scope"] == "platform"
    listee = next(e for e in (await client.get(f"{ORG}/agent-catalogue")).json() if e["slug"] == "reviewer")
    assert listee["own_agent"] is False


async def test_un_agent_du_catalogue_installe_par_un_gabarit_se_met_a_jour_depuis_le_catalogue(
    client: AsyncClient, admin: str, gabarits: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _projet_du_gabarit(client, gabarits)
    _le_catalogue_passe_a_la_version_deux(monkeypatch, "reviewer")
    publie = await client.post(f"{ORG}/agent-catalogue/reviewer/install", json={"upgrade": True})
    assert publie.status_code == 200, publie.text
    assert [v["version"] for v in publie.json()["versions"]] == [2, 1]
    assert publie.json()["versions"][0]["spec"]["instructions"] == "Review it, v2."


async def test_un_agent_diverge_n_est_pas_ecrase_par_la_mise_a_jour(
    client: AsyncClient, admin: str, gabarits: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import Agent, AgentVersion
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _projet_du_gabarit(client, gabarits)
    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == "reviewer"))).scalar_one()
        premiere = (
            await session.execute(select(AgentVersion).where(AgentVersion.agent_id == agent.id))
        ).scalar_one()
        premiere.created_by += "!diverged"
        session.add(
            AgentVersion(agent_id=agent.id, org_id=agent.org_id, version=2, spec=premiere.spec,
                         checksum="sha256:by-hand", created_by="alice@varga.dev")
        )  # fmt: skip
        await session.commit()
    _le_catalogue_passe_a_la_version_deux(monkeypatch, "reviewer")
    refuse = await client.post(f"{ORG}/agent-catalogue/reviewer/install", json={"upgrade": True})
    assert refuse.status_code == 409 and "published its own text" in refuse.text, refuse.text
    assert (await client.get(f"{ORG}/agents/reviewer")).json()["latest_version"] == 2


def test_a_manifest_names_the_catalogue_provenance_of_the_agents_it_takes_from_it() -> None:
    from choregos_api.services.gabarits import livraison

    reviewer = entree("reviewer")
    assert reviewer is not None
    manifest = {"defaults": {"workflows": ["template:default-simple@1"], "agents": ["catalogue:reviewer"]}}
    assert livraison(manifest, None).agents_du_catalogue == {
        "reviewer": f"plugin:software-delivery/reviewer@{reviewer.version}"
    }
