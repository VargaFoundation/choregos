"""Le chart sait-il parler à la plateforme d'observabilité qui l'héberge ?

Le 2026-09-26, le locataire dev de Diametral tournait depuis une heure avec un `ServiceMonitor`
et une `PrometheusRule` bien présents dans son namespace — et **pas une série collectée, pas une
alerte évaluée**. La plateforme tourne sur VictoriaMetrics : son opérateur ne regarde pas les
types `monitoring.coreos.com`. Les objets étaient inertes, et un objet inerte est pire qu'un
objet absent, parce qu'il donne la sensation d'être surveillé.

Quatre propriétés. La quatrième est la vraie : les règles d'alerte sont écrites une fois et
servies aux deux saveurs, donc elles ne peuvent pas diverger.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

RACINE = pathlib.Path(__file__).resolve().parents[2]
CHART = RACINE / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _rendu(*surcharges: str) -> list[dict[str, Any]]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    sortie = subprocess.run(commande, capture_output=True, text=True, check=True, timeout=120)
    return [doc for doc in yaml.safe_load_all(sortie.stdout) if doc]


def _objets(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [doc for doc in docs if doc.get("kind") == kind]


def test_la_saveur_par_defaut_reste_prometheus_operator() -> None:
    """Changer le défaut casserait toutes les installations existantes."""
    docs = _rendu()
    assert _objets(docs, "ServiceMonitor"), "plus de ServiceMonitor par défaut"
    assert _objets(docs, "PrometheusRule"), "plus de PrometheusRule par défaut"
    assert not _objets(docs, "VMServiceScrape") and not _objets(docs, "VMRule")


def test_la_saveur_victoriametrics_ne_rend_que_des_types_victoriametrics() -> None:
    docs = _rendu("monitoring.flavour=victoriametrics")
    scrapes, regles = _objets(docs, "VMServiceScrape"), _objets(docs, "VMRule")
    assert scrapes, "aucun VMServiceScrape : rien ne sera collecté"
    assert regles, "aucun VMRule : aucune alerte ne sera évaluée"
    for objet in scrapes + regles:
        assert objet["apiVersion"] == "operator.victoriametrics.com/v1beta1"
    restes = [
        doc["kind"] for doc in docs if str(doc.get("apiVersion", "")).startswith("monitoring.coreos.com")
    ]
    assert not restes, f"types inertes rendus en même temps : {restes}"


def test_il_n_y_a_qu_un_seul_vmrule() -> None:
    """Deux VMRule dans une même portée de vmalert font boucler l'opérateur — la plateforme
    d'accueil l'a déjà payé une fois, et le contrat d'intégration l'écrit noir sur blanc."""
    assert len(_objets(_rendu("monitoring.flavour=victoriametrics"), "VMRule")) == 1


def test_les_deux_saveurs_portent_exactement_les_memes_alertes() -> None:
    """Sans cette garde, une alerte ajoutée d'un côté manquerait de l'autre, en silence."""
    prometheus = _objets(_rendu(), "PrometheusRule")[0]["spec"]["groups"]
    victoria = _objets(_rendu("monitoring.flavour=victoriametrics"), "VMRule")[0]["spec"]["groups"]
    assert prometheus == victoria, "les règles ont divergé entre les deux saveurs"
    noms = [regle["alert"] for groupe in prometheus for regle in groupe["rules"]]
    assert len(noms) == 5, f"5 alertes attendues, {len(noms)} rendues : {noms}"


def test_une_saveur_inconnue_arrete_le_rendu_et_se_nomme() -> None:
    """Une faute de frappe ne doit pas produire un déploiement silencieusement aveugle."""
    echec = subprocess.run(
        ["helm", "template", "choregos", str(CHART), "--set", "monitoring.flavour=victoria"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert echec.returncode != 0, "une saveur inconnue passe : le déploiement serait aveugle"
    assert "monitoring.flavour=victoria" in echec.stderr
