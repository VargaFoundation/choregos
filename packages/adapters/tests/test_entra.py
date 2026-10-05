"""Le connecteur `entra` (Microsoft Graph, S20-03) contre un faux Graph : chaque geste rejouable
sans double effet, l'unité administrative tenue, `Retry-After` respecté."""

from __future__ import annotations

import pytest
from choregos_adapters.fakes.entra import FakeEntra
from choregos_adapters.identity import EntraIdentity, HorsDeLUnite


def _annuaire(graph: FakeEntra) -> EntraIdentity:
    return EntraIdentity(
        tenant_id="acme",
        client_id="choregos",
        client_secret="secret",
        administrative_unit_id=graph.unite,
        transport=graph.transport(),
    )


async def test_une_double_creation_donne_un_seul_compte_dans_l_unite() -> None:
    graph = FakeEntra()
    annuaire = _annuaire(graph)
    premier = await annuaire.create_user("lea.martin@acme.test", "Léa Martin")
    second = await annuaire.create_user("lea.martin@acme.test", "Léa Martin")
    assert premier["created"] is True and second["created"] is False
    assert premier["id"] == second["id"]
    assert len(graph.comptes) == 1
    assert premier["id"] in graph.membres_de_l_unite, "un compte créé entre dans l'unité"


async def test_ajouter_deux_fois_au_meme_groupe_est_un_succes() -> None:
    graph = FakeEntra()
    annuaire = _annuaire(graph)
    await annuaire.create_user("lea.martin@acme.test", "Léa Martin")
    assert (await annuaire.add_to_group("lea.martin@acme.test", "devs"))["added"] is True
    assert (await annuaire.add_to_group("lea.martin@acme.test", "devs"))["added"] is False
    assert (await annuaire.remove_from_group("lea.martin@acme.test", "devs"))["removed"] is True
    assert (await annuaire.remove_from_group("lea.martin@acme.test", "devs"))["removed"] is False


async def test_un_compte_hors_de_l_unite_est_refuse_nomme() -> None:
    graph = FakeEntra()
    graph.ajouter_compte("pdg@acme.test", dans_l_unite=False)
    annuaire = _annuaire(graph)
    with pytest.raises(HorsDeLUnite, match="unité administrative"):
        await annuaire.disable_user("pdg@acme.test")
    with pytest.raises(HorsDeLUnite):
        await annuaire.revoke_sessions("pdg@acme.test")
    assert graph.comptes and all(c["accountEnabled"] for c in graph.comptes.values()), "rien n'a bougé"


async def test_un_depart_desactive_et_revoque(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = FakeEntra()
    annuaire = _annuaire(graph)
    await annuaire.executer("create_user", {"upn": "paul@acme.test", "display_name": "Paul"})
    await annuaire.executer("disable_user", {"upn": "paul@acme.test"})
    await annuaire.executer("revoke_sessions", {"upn": "paul@acme.test"})
    (compte,) = graph.comptes.values()
    assert compte["accountEnabled"] is False
    assert graph.sessions_revoquees == ["paul@acme.test"]
    assert (await annuaire.executer("get_user", {"upn": "absent@acme.test"})) == {"user": None}


async def test_un_429_est_attendu_puis_repris() -> None:
    graph = FakeEntra(trop_de_requetes=2)
    annuaire = _annuaire(graph)
    assert (await annuaire.create_user("lea@acme.test", "Léa"))["created"] is True
    assert sum(1 for m, c in graph.recues if c.endswith("/users/lea@acme.test")) >= 3, (
        "deux 429, puis la réponse"
    )
