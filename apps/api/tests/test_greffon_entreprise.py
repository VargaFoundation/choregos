# SPDX-License-Identifier: Apache-2.0
"""Un greffon d'édition entreprise, installé hors de l'arbre, tient-il tout ce qu'on lui demande ?

C'est **le prérequis du jalon E4** et de la décision D-E1 (l'édition entreprise vit dans un dépôt
privé séparé). Tout l'édifice repose sur une seule phrase : « le paquet `choregos-ee` s'installe à
côté, se déclare par un point d'entrée `choregos.plugins`, et la plateforme fait le reste sans
qu'on la patche ». Tant que personne ne l'a essayé, cette phrase est une intention.

Ce qui était déjà éprouvé (`packages/adapters/tests/test_greffons.py`) : un **connecteur** déclaré
par un vrai `.dist-info` est bien trouvé par `importlib.metadata`, et un greffon qui casse arrête
le processus. Ce qui ne l'était pas, et c'est le cœur du modèle commercial :

* **l'édition** — `declarer(ENTREPRISE)` appelé depuis le point d'entrée, pas depuis le test ;
* **le démarrage** — l'API refuse `CHOREGOS_EDITION=enterprise` quand rien ne s'est déclaré
  (#107). Il faut donc qu'un vrai greffon lève ce refus, sinon la garde est un mur ;
* **la seconde organisation** — le `409` de l'édition communautaire doit s'ouvrir ;
* **le mappeur de groupes** (couture C3) et une **garantie**, tous deux par le point d'entrée.

Les tests précédents appelaient `declarer(...)` et `declarer_le_mappeur_de_groupes(...)`
directement : ils prouvaient que les fonctions marchent, pas que le CHEMIN marche.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest

#: Un `choregos-ee` crédible en miniature : il fait les quatre gestes que l'édition entreprise
#: fera vraiment, et rien d'autre. Écrit comme un vrai module, parce qu'il en sera un.
GREFFON_EE = '''
from choregos_adapters import register
from choregos_api.edition import ENTREPRISE, declarer, declarer_le_mappeur_de_groupes
from choregos_contracts import Role
from choregos_core.gates import GateOutcome, gate


class ExecuteurSousBac:
    """Un exécuteur d'édition entreprise (ADR 0016 : cluster-scoped, donc hors du CE)."""

    def __init__(self, config):
        self.config = config


def mappeur_par_organisation(groups, default_org):
    """Un préfixe PAR organisation : `acme/admin` en PLUS de `choregos:acme:admin`.

    Il DÉLÈGUE au mappeur du cœur pour le reste, et ce n'est pas une politesse : un mappeur qui
    remplace sans comprendre le format du cœur fait perdre au déploiement ses propres connexions.
    Le premier jet de ce greffon ne déléguait pas, et la connexion de développement ne donnait
    plus aucun rôle — `403 il manque ['varga']` sur la création d'organisation.
    """
    from choregos_api.routers.auth import roles_des_groupes

    roles = dict(roles_des_groupes(groups, default_org))
    for groupe in groups:
        if "/" in groupe:
            org, nom = groupe.split("/", 1)
            if nom == "admin":
                roles[org] = Role.ORG_ADMIN
    return roles


@gate("attestation_signee")
def attestation_signee(ctx):
    """Une garantie d'édition entreprise (ADR 0015)."""
    return GateOutcome(name="attestation_signee", passed=True, detail="signée")


def brancher():
    declarer(ENTREPRISE, fonctions=frozenset({"multi_org", "attestation"}))
    declarer_le_mappeur_de_groupes(mappeur_par_organisation)
    register("runtime", "agent-sandbox")(lambda cfg: ExecuteurSousBac(cfg))
'''


def _installer(racine: pathlib.Path, nom: str, source: str) -> None:
    """Écrit un module et le `.dist-info` qui le déclare — comme le ferait `pip install`."""
    (racine / f"{nom}.py").write_text(source, encoding="utf-8")
    info = racine / f"{nom}-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {nom}\nVersion: 0.1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(f"[choregos.plugins]\n{nom} = {nom}:brancher\n", encoding="utf-8")


@pytest.fixture
def greffon_ee_installe(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    """Installe le greffon, et **nettoie tout** : l'édition et le mappeur sont des états de module."""
    from choregos_api.edition import reinitialiser

    sys.path.insert(0, str(tmp_path))
    _installer(tmp_path, "choregos_ee_factice", GREFFON_EE)
    try:
        yield tmp_path
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("choregos_ee_factice", None)
        reinitialiser()


def test_le_greffon_declare_son_edition_par_le_point_d_entree(greffon_ee_installe: pathlib.Path) -> None:
    """Le geste dont dépend tout le modèle : l'édition vient du PAQUET, pas d'un appel de test."""
    from choregos_adapters import available, charger_les_greffons
    from choregos_api.edition import courante, est_entreprise, fonctions

    assert not est_entreprise(), "le test partirait d'un état déjà pollué"

    charges = charger_les_greffons()
    assert "choregos_ee_factice" in charges

    assert courante() == "enterprise"
    assert est_entreprise()
    assert fonctions() == frozenset({"multi_org", "attestation"})
    # Et son exécuteur d'édition entreprise est disponible comme n'importe quel connecteur.
    assert "agent-sandbox" in available("runtime")


def test_l_api_demarre_alors_qu_elle_refusait_sans_greffon(
    greffon_ee_installe: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La garde de la #107 doit être un contrôle, pas un mur.

    Elle refuse `CHOREGOS_EDITION=enterprise` quand aucun greffon ne s'est déclaré. Si un vrai
    greffon ne la levait pas, la garde interdirait l'édition entreprise au lieu de la vérifier —
    et personne ne s'en apercevrait avant d'avoir un `choregos-ee` à déployer.
    """
    from choregos_api.config import reset_settings_cache
    from choregos_api.edition import reinitialiser
    from choregos_api.main import _verifier_l_edition, create_app

    monkeypatch.setenv("CHOREGOS_EDITION", "enterprise")
    reset_settings_cache()
    try:
        # Sans greffon chargé : refus, avec la cause nommée.
        reinitialiser()
        with pytest.raises(RuntimeError, match="choregos-ee"):
            _verifier_l_edition(__import__("choregos_api.config", fromlist=["get_settings"]).get_settings())
        # Avec : l'application se construit.
        assert create_app() is not None
    finally:
        monkeypatch.delenv("CHOREGOS_EDITION", raising=False)
        reset_settings_cache()


@pytest.mark.asyncio
async def test_le_greffon_ouvre_la_seconde_organisation(
    greffon_ee_installe: pathlib.Path, client: Any, org: str, admin: str
) -> None:
    """Le `409` de l'édition communautaire est la seule chose qu'on ait RETIRÉE au CE (§2.2).

    Il doit donc s'ouvrir par le greffon, et par lui seul.
    """
    from choregos_adapters import charger_les_greffons

    charger_les_greffons()
    reponse = await client.post("/api/v1/orgs", json={"slug": "seconde", "name": "Seconde"})
    assert reponse.status_code == 201, reponse.text


def test_le_greffon_remplace_le_mappeur_et_la_garantie(greffon_ee_installe: pathlib.Path) -> None:
    """Couture C3 et couture des garanties, par le CHEMIN réel cette fois."""
    from choregos_adapters import charger_les_greffons
    from choregos_api.edition import mappeur_de_groupes
    from choregos_api.routers.auth import roles_des_groupes
    from choregos_contracts import Role
    from choregos_core.gates import known_gates

    charger_les_greffons()

    mappeur = mappeur_de_groupes(roles_des_groupes)
    assert mappeur is not roles_des_groupes
    # Le préfixe de l'entreprise, que le cœur ne sait pas lire.
    assert mappeur(["acme/admin", "bruit"], "varga") == {"acme": Role.ORG_ADMIN}
    # Et le cœur, lui, ne comprend pas cette forme — ce qui prouve que c'est bien l'autre qui sert.
    assert roles_des_groupes(["acme/admin"], "varga") == {}
    # Mais le format du CŒUR continue de marcher : un mappeur d'entreprise DÉLÈGUE, sinon le
    # déploiement perd ses propres connexions (vu en écrivant ce test : 403 sur `POST /orgs`).
    assert mappeur(["choregos:varga:org_admin"], "varga") == roles_des_groupes(
        ["choregos:varga:org_admin"], "varga"
    )

    # La garantie du greffon est connue du validateur : sans ça, un workflow qui l'emploie est
    # invalide (`gate.unknown` est une erreur bloquante) et rien ne s'exécute jamais.
    assert "attestation_signee" in known_gates()
