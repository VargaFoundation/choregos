"""Les connecteurs de l'organisation, et la politique de chaque opération (ADR 0034, S19-02).

L'administrateur de l'organisation déclare une instance et décide, opération par opération, si
elle est permise, soumise à validation ou interdite, et pour quels groupes de projets. Un projet
ne fait que resserrer.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login


@pytest.fixture
def annuaire() -> Iterator[None]:
    """Un type d'annuaire, déclaré comme un greffon le ferait : ses opérations font sa politique."""
    from choregos_adapters import ConnectorTypeSpec, OperationSpec, register
    from choregos_adapters.registry import _REGISTRY, _SPECS

    spec = ConnectorTypeSpec(
        "annuaire de test",
        ("identity",),
        {"type": "object", "properties": {"tenant": {"type": "string"}}},
        ("client_secret",),
        operations=(
            OperationSpec("lire_utilisateur", "read"),
            OperationSpec("creer_compte", "write", "crée un compte"),
            OperationSpec("desactiver_compte", "write"),
        ),
    )
    register("identity", "annuaire", spec)(lambda cfg: object())
    yield
    _REGISTRY.pop(("identity", "annuaire"), None)
    _SPECS.pop(("identity", "annuaire"), None)


async def _declarer(client: AsyncClient, **extra: Any) -> Any:
    corps = {"name": "annuaire-acme", "type": "annuaire", "config": {"tenant": "acme"},
             "secret_refs": {"client_secret": "env:ANNUAIRE_SECRET"}, **extra}  # fmt: skip
    return await client.post("/api/v1/orgs/varga/connectors", json=corps)


def _politiques(operations: list[dict[str, Any]]) -> dict[str, str]:
    return {o["operation"]: o["effective_policy"] for o in operations}


async def test_une_instance_nait_avec_ses_operations_a_leur_politique_par_defaut(
    client: AsyncClient, admin: str, annuaire: None
) -> None:
    cree = await _declarer(client)
    assert cree.status_code == 201, cree.text
    assert cree.json()["kind"] == "identity"
    assert {o["name"]: o["policy"] for o in cree.json()["operations"]} == {
        "creer_compte": "approval",
        "desactiver_compte": "approval",
        "lire_utilisateur": "allowed",
    }
    assert (await _declarer(client)).status_code == 409
    clair = await client.post(
        "/api/v1/orgs/varga/connectors",
        json={"name": "autre", "type": "annuaire", "config": {"client_secret": "s3cr3t"}},
    )
    assert clair.status_code == 422 and "s3cr3t" not in clair.text


async def test_un_projet_resserre_mais_ne_passe_jamais_approval_en_allowed(
    client: AsyncClient, project: dict[str, Any], annuaire: None
) -> None:
    pid = project["id"]
    assert (await _declarer(client)).status_code == 201
    base = f"/api/v1/projects/{pid}/operations/annuaire-acme"
    assert _politiques((await client.get(f"/api/v1/projects/{pid}/operations")).json()) == {
        "creer_compte": "approval",
        "desactiver_compte": "approval",
        "lire_utilisateur": "allowed",
    }
    elargi = await client.put(f"{base}/creer_compte", json={"policy": "allowed"})
    assert elargi.status_code == 422 and "resserrer" in elargi.text

    resserre = await client.put(f"{base}/lire_utilisateur", json={"policy": "approval"})
    assert resserre.status_code == 200, resserre.text
    assert resserre.json()["effective_policy"] == "approval"
    assert (await client.put(f"{base}/desactiver_compte", json={"policy": "forbidden"})).status_code == 200
    vues = _politiques((await client.get(f"/api/v1/projects/{pid}/operations")).json())
    assert vues == {
        "creer_compte": "approval",
        "desactiver_compte": "forbidden",
        "lire_utilisateur": "approval",
    }

    # L'organisation durcit ensuite : le projet suit, sa propre ligne ne l'en dispense pas.
    durcie = await client.patch("/api/v1/orgs/varga/connectors/annuaire-acme/operations/lire_utilisateur",
                                json={"policy": "forbidden"})  # fmt: skip
    assert durcie.status_code == 200
    assert (
        _politiques((await client.get(f"/api/v1/projects/{pid}/operations")).json())["lire_utilisateur"]
        == "forbidden"
    )

    assert (await client.delete(f"{base}/desactiver_compte")).status_code == 204
    assert (
        _politiques((await client.get(f"/api/v1/projects/{pid}/operations")).json())["desactiver_compte"]
        == "approval"
    )


async def test_les_groupes_d_une_operation_disent_quels_projets_la_voient(
    client: AsyncClient, project: dict[str, Any], annuaire: None
) -> None:
    pid = project["id"]
    assert (await _declarer(client)).status_code == 201
    groupes = await client.patch("/api/v1/orgs/varga/connectors/annuaire-acme/operations/creer_compte",
                                 json={"groups": ["rh"]})  # fmt: skip
    assert groupes.status_code == 200 and groupes.json()["groups"] == ["rh"]
    assert "creer_compte" not in _politiques((await client.get(f"/api/v1/projects/{pid}/operations")).json())
    config = {**project["config"], "groups": ["rh"]}
    assert (await client.patch(f"/api/v1/projects/{pid}", json={"config": config})).status_code == 200
    assert "creer_compte" in _politiques((await client.get(f"/api/v1/projects/{pid}/operations")).json())


async def test_un_project_owner_ne_decide_ni_des_connecteurs_ni_des_groupes(
    client: AsyncClient, project: dict[str, Any], annuaire: None
) -> None:
    assert (await _declarer(client)).status_code == 201
    membre = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "po@varga.dev", "role": "project_owner"}
    )
    assert membre.status_code in {200, 201}, membre.text
    await login(client, "po@varga.dev")
    groupes = await client.patch("/api/v1/orgs/varga/connectors/annuaire-acme/operations/creer_compte",
                                 json={"groups": ["finance"]})  # fmt: skip
    assert groupes.status_code == 403
    assert (await client.patch("/api/v1/orgs/varga/connectors/annuaire-acme/operations/creer_compte",
                               json={"policy": "allowed"})).status_code == 403  # fmt: skip
    assert (await _declarer(client, name="pirate")).status_code == 403
    # Il lit, et il resserre son projet.
    assert (await client.get("/api/v1/orgs/varga/connectors")).status_code == 200
    resserre = await client.put(
        f"/api/v1/projects/{project['id']}/operations/annuaire-acme/lire_utilisateur",
        json={"policy": "approval"},
    )
    assert resserre.status_code == 200, resserre.text


async def test_une_autre_organisation_n_existe_pas(client: AsyncClient, admin: str, annuaire: None) -> None:
    from choregos_api.db.models import Organization, OrgConnector
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        autre = Organization(slug="autre", name="Autre")
        session.add(autre)
        await session.flush()
        session.add(
            OrgConnector(org_id=autre.id, name="secret-d-autre", kind="identity", type="annuaire", config={})
        )
    assert (await _declarer(client)).status_code == 201
    noms = [c["name"] for c in (await client.get("/api/v1/orgs/varga/connectors")).json()]
    assert noms == ["annuaire-acme"]
    assert (await client.get("/api/v1/orgs/autre/connectors")).status_code == 403
