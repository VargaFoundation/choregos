"""`POST /workflows/edit` : des opérations typées, greffées dans le texte (ADR 0031, S16-11).

La console modifie un workflow depuis la carte sans réécrire son YAML : la réponse porte le texte
édité, son diff, la validation, le graphe et l'inverse — qui redonne les octets d'origine.
"""

from __future__ import annotations

from typing import Any

from choregos_core.dsl import template_yaml
from httpx import AsyncClient

ORIGINE = template_yaml("default-simple")


async def _editer(client: AsyncClient, texte: str, *operations: dict[str, Any]) -> Any:
    return await client.post("/api/v1/workflows/edit", json={"yaml": texte, "operations": list(operations)})


async def test_une_garantie_ajoutee_se_relit_en_diff_et_s_annule(client: AsyncClient, admin: str) -> None:
    reponse = await _editer(
        client, ORIGINE, {"op": "add_gate", "transition": "t-implement", "gate": "ci_green", "index": 0}
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["valid"] is True and corps["graph"]["nodes"]
    # La vue processus du texte édité (S16-08) : la garantie ajoutée y est dite.
    (etape,) = [e for e in corps["process"] if e["id"] == "t-implement"]
    assert "ci_green" in {g["name"] for g in etape["gates"]}
    modifiees = [
        ligne
        for ligne in corps["diff"].splitlines()
        if ligne.startswith(("+", "-")) and not ligne.startswith(("+++", "---"))
    ]
    retiree, ajoutee = modifiees  # une ligne change, le reste ne bouge pas
    assert retiree.startswith("-") and "ci_green" not in retiree
    assert ajoutee.startswith("+") and "gates: [ci_green, " in ajoutee
    assert [c for c in ORIGINE.splitlines() if c.lstrip().startswith("#")] == [
        c for c in corps["yaml"].splitlines() if c.lstrip().startswith("#")
    ], "les commentaires restent"
    annulee = await client.post(
        "/api/v1/workflows/edit", json={"yaml": corps["yaml"], "operations": corps["inverse"]}
    )
    assert annulee.json()["yaml"] == ORIGINE


async def test_renommer_un_etat_a_effet_l_ecrit_d_abord(client: AsyncClient, admin: str) -> None:
    """Un workflow écrit avant #175 : `pr_open` ouvre la PR par son NOM. Renommé, l'effet s'écrit
    d'abord (`does: open_pr`) et la réponse le dit ; l'inverse rend le texte d'origine."""
    ancien = ORIGINE.replace("    does: open_pr\n", "")
    assert "does: open_pr" not in ancien
    reponse = await _editer(client, ancien, {"op": "rename_state", "from": "pr_open", "to": "revue"})
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert "does: open_pr" in corps["yaml"] and "pr_open" not in corps["yaml"]
    assert any("is now written" in n for n in corps["notices"])
    annulee = await client.post(
        "/api/v1/workflows/edit", json={"yaml": corps["yaml"], "operations": corps["inverse"]}
    )
    assert annulee.json()["yaml"] == ancien


async def test_ce_qui_ne_s_applique_pas_recoit_422(client: AsyncClient, admin: str) -> None:
    inconnue = await _editer(client, ORIGINE, {"op": "remove_transition", "id": "t-inconnue"})
    assert inconnue.status_code == 422 and "t-inconnue" in inconnue.text
    mal_formee = await _editer(client, ORIGINE, {"op": "fly_to_the_moon"})
    assert mal_formee.status_code == 422, mal_formee.text
    sans_operation = await client.post("/api/v1/workflows/edit", json={"yaml": ORIGINE, "operations": []})
    assert sans_operation.status_code == 422
