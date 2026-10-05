"""Un gabarit livre plusieurs workflows, leur défaut, leur routage et sa politique (ADR 0031, S16-07).

`ensure_defaults` ignorait le manifeste : quel que soit son gabarit, un projet naissait avec
`default-simple` et `solo`. Un gabarit RH ne pouvait donc pas livrer l'arrivée ET le départ.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
import yaml
from httpx import AsyncClient


def _flux(nom: str, etat: str) -> str:
    return f"""apiVersion: choregos/v1
kind: Workflow
metadata: {{name: {nom}, version: 1}}
actors:
  rh: {{type: human, group: rh}}
states:
  {etat}: {{display: Demande, kind: wait}}
  fait: {{display: Fait, terminal: true}}
transitions:
  - {{id: t-faire, from: {etat}, to: fait, by: rh}}
"""


@pytest.fixture
def gabarits(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Un dossier de gabarits à soi ; `ecrire(defaults)` y pose le gabarit `rh`."""
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", str(tmp_path))
    (tmp_path / "secret.yaml").write_text(_flux("fuite", "dehors"), encoding="utf-8")

    def ecrire(defaults: dict[str, Any]) -> None:
        dossier = tmp_path / "rh"
        (dossier / "workflows").mkdir(parents=True, exist_ok=True)
        (dossier / "workflows" / "arrivee.yaml").write_text(_flux("onboarding", "arrivee"), encoding="utf-8")
        (dossier / "workflows" / "depart.yaml").write_text(_flux("offboarding", "depart"), encoding="utf-8")
        manifeste = {
            "apiVersion": "choregos/v1",
            "kind": "Template",
            "metadata": {"name": "rh", "version": "1.0.0", "display": "RH"},
            "requires": {"connectors": {"tracker": "internal"}},
            "defaults": defaults,
            "inputs": [],
            "steps": [],
        }
        (dossier / "manifest.yaml").write_text(yaml.safe_dump(manifeste), encoding="utf-8")

    return ecrire


async def _projet(client: AsyncClient, template_ref: str, slug: str = "rh") -> Any:
    return await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": slug,
            "name": slug,
            "template_ref": template_ref,
            "config": {"slug": slug, "org": "varga"},
        },
    )


ARRIVEE_ET_DEPART = {
    "workflows": ["workflows/arrivee.yaml", "workflows/depart.yaml"],
    "default_workflow": "onboarding",
    "routing": [{"when": {"labels_any": ["leaver"]}, "workflow": "offboarding"}],
    "policy": "preset:team",
}


async def test_un_projet_ne_d_un_gabarit_a_deux_workflows_les_recoit(
    client: AsyncClient, admin: str, gabarits: Any
) -> None:
    gabarits(ARRIVEE_ET_DEPART)
    cree = await _projet(client, "rh@1.0.0")
    assert cree.status_code == 201, cree.text
    pid = cree.json()["id"]

    actifs = {w["name"] for w in (await client.get(f"/api/v1/projects/{pid}/workflows")).json()}
    assert actifs == {"onboarding", "offboarding"}
    routage = (await client.get(f"/api/v1/projects/{pid}/workflow-routing")).json()
    assert routage["default"] == "onboarding"
    assert routage["rules"] == [
        {"when": {"labels_any": ["leaver"], "labels_all": [], "item_type": None}, "workflow": "offboarding"}
    ]
    assert (await client.get(f"/api/v1/projects/{pid}/policy")).json()["name"] == "team"

    # Le routage livré sert : une demande étiquetée `leaver` naît au départ.
    tracker = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    for etiquettes, etat in (([], "arrivee"), (["leaver"], "depart")):
        ticket = await client.post(
            f"/api/v1/projects/{pid}/work-items",
            json={"title": "Camille", "labels": etiquettes, "start": False},
        )
        assert ticket.status_code == 201, ticket.text
        assert ticket.json()["state"] == etat


@pytest.mark.parametrize(
    ("defaults", "motif"),
    [
        pytest.param({"workflows": ["../secret.yaml"]}, "sort du dossier", id="hors-du-dossier"),
        pytest.param({"workflows": ["workflows/absent.yaml"]}, "absent", id="fichier-absent"),
        pytest.param(
            {"workflows": ["workflows/arrivee.yaml"], "routing": [{"when": {}, "workflow": "offboarding"}]},
            "offboarding",
            id="routage-vers-un-workflow-non-livre",
        ),
        pytest.param({"workflows": ["template:inconnu@1"]}, "inconnu", id="gabarit-du-coeur-inconnu"),
        pytest.param(
            {"workflows": ["workflows/arrivee.yaml"], "policy": "/etc/passwd"}, "preset", id="politique"
        ),
    ],
)
async def test_un_gabarit_fautif_ne_donne_pas_de_projet(
    client: AsyncClient, admin: str, gabarits: Any, defaults: dict[str, Any], motif: str
) -> None:
    gabarits(defaults)
    refuse = await _projet(client, "rh@1.0.0")
    assert refuse.status_code == 422, refuse.text
    assert motif in refuse.text
    projets = (await client.get("/api/v1/orgs/varga/projects")).json()["items"]
    assert "rh" not in {p["slug"] for p in projets}, "rien n'est créé à moitié"


async def test_les_gabarits_livres_gardent_leur_forme(client: AsyncClient, admin: str) -> None:
    """`defaults.workflow`, au singulier : la forme des gabarits livrés jusqu'ici."""
    cree = await _projet(client, "github-aca@1.0.0", slug="sur-azure")
    assert cree.status_code == 201, cree.text
    actifs = (await client.get(f"/api/v1/projects/{cree.json()['id']}/workflows")).json()
    assert [w["name"] for w in actifs] == ["default-simple"]


def test_le_schema_des_gabarits_connait_ces_champs() -> None:
    """Un administrateur publie un gabarit en base (`POST /templates`) : le schéma doit l'accepter."""
    import choregos_contracts as contracts
    from jsonschema import Draft202012Validator

    schema = Draft202012Validator(contracts.load_schema("template.schema.json"))
    manifeste = {
        "apiVersion": "choregos/v1",
        "kind": "Template",
        "metadata": {"name": "rh", "version": "1.0.0", "display": "RH"},
        "requires": {"connectors": {"tracker": "internal"}},
        "defaults": ARRIVEE_ET_DEPART,
        "inputs": [],
        "steps": ["github.ensure_labels"],
    }
    assert not list(schema.iter_errors(manifeste))
    sans_cible = {**ARRIVEE_ET_DEPART, "routing": [{"when": {"labels_any": ["leaver"]}}]}
    assert list(schema.iter_errors({**manifeste, "defaults": sans_cible})), (
        "une règle sans workflow est refusée"
    )
