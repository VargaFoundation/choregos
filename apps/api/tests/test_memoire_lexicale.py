"""La mémoire lexicale (le repli sans service de plus) face à une vraie base.

Elle n'avait jamais été exercée sur une base : `lexical_vector` et `similarity` avaient
leurs tests, l'adaptateur aucun. Sur PostgreSQL, il ne voyait d'ailleurs RIEN — la fabrique
lui donnait une session nue, et la RLS fail-closed ne montre rien à une session qui ne
nomme pas son organisation. SQLite (sans RLS) laissait passer.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_core.domain import Fact, Provenance
from httpx import ASGITransport, AsyncClient

from .conftest import login, sans_postgres


@pytest.fixture(autouse=True)
def vrais_adaptateurs(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CHOREGOS_FAKES=1` remplacerait le repli par le fake en mémoire : ici on veut la base."""
    monkeypatch.setenv("CHOREGOS_FAKES", "0")


async def _projet(client: AsyncClient, org: str, slug: str) -> str:
    payload = {"slug": slug, "name": slug, "config": {"slug": slug, "org": org}}
    response = await client.post(f"/api/v1/orgs/{org}/projects", json=payload)
    assert response.status_code == 201, response.text
    project_id = str(response.json()["id"])
    upsert = await client.put(f"/api/v1/projects/{project_id}/connectors/memory", json={"type": "pgvector"})
    assert upsert.status_code in {200, 201}, upsert.text
    return project_id


def _memoire(org: str) -> Any:
    from choregos_adapters import build

    return build("memory", "pgvector", {"org": org})


async def test_ecrit_cherche_propose_et_valide_sur_la_base(client: AsyncClient, org: str) -> None:
    """Le cycle complet, sur SQLite : écriture gouvernée, recherche lexicale, proposition
    d'un agent, validation humaine par l'API — qui lit le connecteur mémoire du projet."""
    await login(client, "admin@varga.dev")
    project_id = await _projet(client, org, "facturation")
    memoire = _memoire(org)

    premier = await memoire.write_fact(
        "facturation",
        Fact(kind="decision", subject="arrondi", content="Les montants s'arrondissent au centime."),
    )
    second = await memoire.write_fact(
        "facturation",
        Fact(kind="decision", subject="arrondi", content="Les montants s'arrondissent au demi-centime."),
    )
    assert premier != second
    trouves = await memoire.search("facturation", "arrondi des montants", k=5)
    assert [m.id for m in trouves] == [second], "le fait précédent de même sujet est superseded"

    propose = await memoire.propose_fact(
        "facturation",
        Fact(kind="run_lesson", subject="tva", content="La TVA se calcule après remise."),
        Provenance(source="agent", run_id="run-1"),
    )
    via_api = await client.get(f"/api/v1/projects/{project_id}/memory/pending")
    assert via_api.status_code == 200, via_api.text
    assert [m["id"] for m in via_api.json()] == [propose]
    assert via_api.json()[0]["proposed_by"] == "run-1"

    decision = await client.post(
        f"/api/v1/projects/{project_id}/memory/pending", json={"id": propose, "action": "accept"}
    )
    assert decision.status_code == 200, decision.text
    assert decision.json() == {"status": "accepted"}

    recherche = await client.get(f"/api/v1/projects/{project_id}/memory/search", params={"q": "TVA remise"})
    assert recherche.status_code == 200, recherche.text
    assert [m["subject"] for m in recherche.json()] == ["tva"]

    pack = await memoire.context_pack("facturation", "tva", paths=[], budget_tokens=500)
    assert [m.subject for m in pack.memories] == ["tva"]
    assert pack.tokens_estimated > 0


async def test_sans_organisation_la_fabrique_refuse(app: Any) -> None:
    from choregos_adapters import build
    from choregos_adapters.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="org"):
        build("memory", "pgvector", {})


@sans_postgres
async def test_sur_postgres_chaque_organisation_ne_voit_que_sa_memoire(pg_app: Any) -> None:
    """Deux organisations, un projet `billing-api` chacune. L'adaptateur de `a` lit et
    écrit chez `a` ; celui de `b` ne voit pas les faits de `a` ; une session nue — l'ancien
    branchement — ne voit même pas le projet."""
    from choregos_adapters.memory.lexicale import LexicalMemory, ModelesMemoire
    from choregos_api.db.models import MemoryFact, Organization, Project
    from choregos_api.db.session import get_sessionmaker, session_scope
    from choregos_api.services import ensure_defaults

    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            organisation = Organization(slug=slug, name=slug.upper())
            session.add(organisation)
            await session.flush()
            projet = Project(
                org_id=organisation.id,
                slug="billing-api",
                name="Billing",
                status="active",
                config={"slug": "billing-api", "org": slug},
            )
            session.add(projet)
            await session.flush()
            await ensure_defaults(session, projet)

    chez_a = _memoire("a")
    chez_b = _memoire("b")
    await chez_a.write_fact(
        "billing-api", Fact(kind="decision", subject="devise", content="Tout est en euros.")
    )

    assert [m.subject for m in await chez_a.search("billing-api", "euros", k=5)] == ["devise"]
    assert await chez_b.search("billing-api", "euros", k=5) == []

    nue = LexicalMemory(get_sessionmaker(), ModelesMemoire(Project=Project, MemoryFact=MemoryFact))
    with pytest.raises(ValueError, match="projet inconnu"):
        await nue.search("billing-api", "euros", k=5)

    # Et par l'API : un développeur de `a` cherche dans SON projet et trouve le fait.
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "admin@a.test")
        upsert = await client.put(
            "/api/v1/projects/a:billing-api/connectors/memory", json={"type": "pgvector"}
        )
        assert upsert.status_code in {200, 201}, upsert.text
        recherche = await client.get("/api/v1/projects/a:billing-api/memory/search", params={"q": "euros"})
        assert recherche.status_code == 200, recherche.text
        assert [m["subject"] for m in recherche.json()] == ["devise"]


def test_les_deux_noms_construisent_le_meme_repli() -> None:
    """`pgvector` est un alias déprécié de `lexical`, et il doit le rester.

    Le nom promettait de la recherche vectorielle : il n'y a aucune extension `vector` dans les
    migrations, la colonne `embedding` est un `Json`, et la similarité est un produit scalaire
    entre sacs de mots calculé en Python. Renommer était honnête ; casser les projets qui ont
    déjà écrit `pgvector` dans leur connecteur ne l'aurait pas été.
    """
    import os

    from choregos_adapters import build
    from choregos_adapters.memory.lexicale import LexicalMemory

    os.environ["CHOREGOS_FAKES"] = "0"
    try:
        for nom in ("lexical", "pgvector"):
            assert isinstance(build("memory", nom, {"org": "*"}), LexicalMemory), nom
    finally:
        os.environ["CHOREGOS_FAKES"] = "1"


async def test_le_catalogue_de_l_interface_ne_propose_plus_l_ancien_nom(client: AsyncClient) -> None:
    """Un nouveau projet ne doit pas pouvoir choisir un nom qui ment."""
    types = (await client.get("/api/v1/connectors/types")).json()
    memoire = {entree["type"] for entree in types if entree["kind"] == "memory"}
    assert "lexical" in memoire
    assert "pgvector" not in memoire, "l'ancien nom est encore proposé à la création"
