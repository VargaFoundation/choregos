"""Les familles métier dans l'organisation (S20-04) : un parc, un transporteur, des lecteurs de badges
se déclarent comme tout connecteur — la clé en référence —, leurs opérations naissent avec leur
politique, et une écriture approuvée atteint le système avec la clé résolue par la plateforme.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"
CLE = "cle-du-parc-demo"


@pytest.fixture
def parc(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.rh import FakeMdm

    faux = FakeMdm(cle_attendue=CLE)
    monkeypatch.setitem(choregos_adapters.FAUX_METIER, "mdm", faux)
    monkeypatch.setenv("CHOREGOS_TEST_CLE_PARC", CLE)
    yield faux


async def _declarer(client: AsyncClient, kind: str, nom: str) -> dict[str, Any]:
    corps = {
        "name": nom,
        "kind": kind,
        "type": "demo",
        "secret_refs": {"api_key": "env:CHOREGOS_TEST_CLE_PARC"},
    }
    cree = await client.post(f"{ORG}/connectors", json=corps)
    assert cree.status_code == 201, cree.text
    assert CLE not in cree.text, "une référence, jamais la valeur"
    return {o["name"]: o["policy"] for o in cree.json()["operations"]}


@pytest.mark.parametrize(
    ("kind", "attendu"),
    [
        ("mdm", {"enroll_device": "approval", "wipe_device": "approval", "device_status": "allowed"}),
        (
            "shipping",
            {"create_shipment": "approval", "create_return": "approval", "shipment_status": "allowed"},
        ),
        (
            "access_control",
            {"activate_badge": "approval", "deactivate_badge": "approval", "badge_status": "allowed"},
        ),
    ],
)
async def test_une_famille_se_declare_cle_en_reference_et_ses_ecritures_passent_par_une_validation(
    client: AsyncClient, admin: str, kind: str, attendu: dict[str, str]
) -> None:
    assert await _declarer(client, kind, f"{kind.replace('_', '-')}-demo") == attendu
    en_clair = await client.post(
        f"{ORG}/connectors", json={"name": "autre", "kind": kind, "type": "demo", "config": {"api_key": CLE}}
    )
    assert en_clair.status_code == 422 and "in clear" in en_clair.text


async def test_une_ecriture_approuvee_atteint_le_parc_avec_la_cle_resolue_et_un_refus_est_definitif(
    client: AsyncClient, project: dict[str, Any], parc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope
    from choregos_api.effets import ContexteEffet, EffetRefuse, effet

    await _declarer(client, "mdm", "parc")
    appel = effet("connector.call")

    def inscrire(upn: str) -> dict[str, Any]:
        return {
            "connector": "parc",
            "operation": "enroll_device",
            "arguments": {"serial": "PC-1", "upn": upn},
        }

    async with session_scope() as session:
        projet = await session.get(Project, project["id"])
        assert projet is not None
        action = Action(
            org_id=projet.org_id,
            project_id=projet.id,
            origin="transition",
            kind="arrivee.poste",
            title="Le poste de Léa",
            params={},
            effects=[],
            proposed_by={"kind": "system", "id": "system"},
            approval={},
            decisions=[],
            status="approved",
        )
        session.add(action)
        await session.flush()
        ctx = ContexteEffet(session, action, projet)
        fait = await appel(ctx, inscrire("lea@acme.test"))
        assert fait["enrolled"] is True and CLE not in str(fait)
        assert parc.postes["PC-1"]["upn"] == "lea@acme.test"
        # Le poste est à Léa : l'inscrire pour Paul est un refus, définitif — Temporal ne retente pas.
        with pytest.raises(EffetRefuse, match="inscrit pour un autre utilisateur"):
            await appel(ctx, inscrire("paul@acme.test"))
        monkeypatch.setenv("CHOREGOS_TEST_CLE_PARC", "une-cle-revoquee")
        with pytest.raises(EffetRefuse, match="clé refusée"):
            await appel(ctx, inscrire("lea@acme.test"))
    assert parc.postes["PC-1"]["upn"] == "lea@acme.test"
