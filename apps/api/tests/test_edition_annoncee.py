"""Ce que le déploiement annonce doit être ce qui est chargé (ADR 0024).

L'édition qui tourne n'est pas un réglage : c'est le greffon entreprise qui la déclare en se
chargeant (`edition.declarer`). Le chart, lui, ANNONCE une édition (`global.edition` →
`CHOREGOS_EDITION`). Rien ne reliait les deux, et l'écart était silencieux : une plateforme
annoncée entreprise sur des images communautaires démarre, paraît saine, expose `/metrics`, et
refuse la seconde organisation des semaines plus tard — un symptôme sans rapport apparent avec sa
cause.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _edition_propre() -> Any:
    """Chaque test repart de l'édition la plus restrictive : l'état est un module."""
    from choregos_api.edition import reinitialiser

    reinitialiser()
    yield
    reinitialiser()


def test_annoncer_entreprise_sans_greffon_refuse_le_demarrage(monkeypatch: Any) -> None:
    """Le refus est au démarrage, et il nomme les trois causes possibles."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.main import create_app

    monkeypatch.setenv("CHOREGOS_EDITION", "enterprise")
    reset_settings_cache()
    try:
        with pytest.raises(RuntimeError) as echec:
            create_app()
        message = str(echec.value)
        assert "global.edition=enterprise" in message
        assert "choregos-ee" in message, "le refus doit dire quel paquet manque"
        assert "community" in message, "le refus doit dire comment revenir en arrière"
    finally:
        monkeypatch.delenv("CHOREGOS_EDITION", raising=False)
        reset_settings_cache()


def test_annoncer_communautaire_demarre(monkeypatch: Any) -> None:
    """Le défaut ne doit rien exiger : c'est l'édition de ce dépôt."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.main import create_app

    monkeypatch.setenv("CHOREGOS_EDITION", "community")
    reset_settings_cache()
    try:
        assert create_app() is not None
    finally:
        monkeypatch.delenv("CHOREGOS_EDITION", raising=False)
        reset_settings_cache()


def test_annoncer_entreprise_avec_un_greffon_qui_se_declare_demarre(monkeypatch: Any) -> None:
    """Un greffon qui appelle `declarer(ENTREPRISE)` lève le refus : c'est le cas réel."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.edition import ENTREPRISE, declarer
    from choregos_api.main import create_app

    monkeypatch.setenv("CHOREGOS_EDITION", "enterprise")
    reset_settings_cache()
    declarer(ENTREPRISE, fonctions=frozenset({"multi_org"}))
    try:
        assert create_app() is not None
    finally:
        monkeypatch.delenv("CHOREGOS_EDITION", raising=False)
        reset_settings_cache()


@pytest.mark.asyncio
async def test_les_metriques_disent_l_edition(client: Any) -> None:
    """`choregos_edition_info` : sans elle, l'historique des métriques ne dit pas sur quoi on tourne.

    Un exploitant qui regarde un tableau de bord d'il y a trois semaines doit pouvoir savoir quelle
    édition a produit ces chiffres. `GET /edition` ne répond que pour le processus d'aujourd'hui.
    """
    from choregos_api.metriques import rafraichir

    await rafraichir()
    corps = (await client.get("/metrics")).text
    assert 'choregos_edition_info{edition="community",version=' in corps
    assert corps.count("choregos_edition_info{") == 1, "une seule série : ce n'est pas une mesure"
