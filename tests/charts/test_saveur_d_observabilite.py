"""Does the chart know how to talk to the observability platform that hosts it?

On 2026-09-26, Diametral's dev tenant had been running for an hour with a `ServiceMonitor`
and a `PrometheusRule` present in its namespace -- and **not one series collected, not one
alert evaluated**. The platform runs on VictoriaMetrics: its operator does not watch the
`monitoring.coreos.com` types. The objects were inert, and an inert object is worse than an
absent one, because it gives the feeling of being monitored.

Four properties. The fourth is the real one: the alert rules are written once and
served to both flavours, so they cannot diverge.
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
    """Changing the default would break every existing installation."""
    docs = _rendu()
    assert _objets(docs, "ServiceMonitor"), "no more ServiceMonitor by default"
    assert _objets(docs, "PrometheusRule"), "no more PrometheusRule by default"
    assert not _objets(docs, "VMServiceScrape") and not _objets(docs, "VMRule")


def test_la_saveur_victoriametrics_ne_rend_que_des_types_victoriametrics() -> None:
    docs = _rendu("monitoring.flavour=victoriametrics")
    scrapes, regles = _objets(docs, "VMServiceScrape"), _objets(docs, "VMRule")
    assert scrapes, "no VMServiceScrape: nothing will be collected"
    assert regles, "no VMRule: no alert will be evaluated"
    for objet in scrapes + regles:
        assert objet["apiVersion"] == "operator.victoriametrics.com/v1beta1"
    restes = [
        doc["kind"] for doc in docs if str(doc.get("apiVersion", "")).startswith("monitoring.coreos.com")
    ]
    assert not restes, f"inert types rendered at the same time: {restes}"


def test_il_n_y_a_qu_un_seul_vmrule() -> None:
    """Two VMRules in the same vmalert scope make the operator loop -- the host platform
    has already paid for it once, and the integration contract spells it out in black and white."""
    assert len(_objets(_rendu("monitoring.flavour=victoriametrics"), "VMRule")) == 1


def test_les_deux_saveurs_portent_exactement_les_memes_alertes() -> None:
    """Without this guard, an alert added on one side would be missing on the other, silently."""
    prometheus = _objets(_rendu(), "PrometheusRule")[0]["spec"]["groups"]
    victoria = _objets(_rendu("monitoring.flavour=victoriametrics"), "VMRule")[0]["spec"]["groups"]
    assert prometheus == victoria, "the rules diverged between the two flavours"
    noms = [regle["alert"] for groupe in prometheus for regle in groupe["rules"]]
    assert len(noms) == 5, f"5 alertes attendues, {len(noms)} rendues : {noms}"


def test_une_saveur_inconnue_arrete_le_rendu_et_se_nomme() -> None:
    """A typo must not produce a silently blind deployment."""
    echec = subprocess.run(
        ["helm", "template", "choregos", str(CHART), "--set", "monitoring.flavour=victoria"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert echec.returncode != 0, "an unknown flavour passes: the deployment would be blind"
    assert "monitoring.flavour=victoria" in echec.stderr
