"""Un métier peut-il apporter son propre template de workflow ?

Jusqu'au 2026-09-26, non : `TEMPLATE_NAMES` était figé à trois noms et `template_path` refusait
tout le reste. La seule issue était de poster le YAML complet par `PUT /projects/{id}/workflow` —
donc sans nom, sans réutilisation, et sans que deux projets d'une même maison puissent partir du
même modèle.

C'est la même couture que les playbooks (`CHOREGOS_PLAYBOOKS_DIR`), et pour la même raison : les
états et les transitions d'un métier n'ont rien à faire dans ce paquet, alors que le reste de la
machine — budgets, garanties, mémoire — ne change pas d'un domaine à l'autre.

La propriété qui compte est la troisième : **le déploiement l'emporte sur le paquet**. Sans elle,
adapter `default-simple` à une maison obligerait à réécrire la plateforme.
"""

from __future__ import annotations

import pathlib

import pytest
from choregos_core.dsl import (
    TEMPLATE_NAMES,
    load_template,
    oublier_les_templates,
    template_names,
    template_path,
    template_yaml,
)


@pytest.fixture(autouse=True)
def cache_propre() -> None:
    oublier_les_templates()


def _ecrire(dossier: pathlib.Path, nom: str, etat_final: str = "done") -> pathlib.Path:
    """Un workflow minimal mais VALIDE : le test doit échouer sur la couture, pas sur le DSL."""
    chemin = dossier / f"{nom}.yaml"
    chemin.write_text(template_yaml("default-simple"), encoding="utf-8")
    return chemin


def test_sans_reglage_seuls_les_templates_du_paquet_existent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHOREGOS_WORKFLOW_TEMPLATES_DIR", raising=False)
    assert set(template_names()) == set(TEMPLATE_NAMES)


def test_un_template_du_deploiement_devient_disponible(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ecrire(tmp_path, "instruction-dossier")
    monkeypatch.setenv("CHOREGOS_WORKFLOW_TEMPLATES_DIR", str(tmp_path))

    assert "instruction-dossier" in template_names()
    assert set(TEMPLATE_NAMES) <= set(template_names()), "les templates livrés restent là"
    assert load_template("instruction-dossier").states, "il doit se charger, pas seulement se lister"


def test_le_deploiement_l_emporte_sur_le_paquet(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adapter un template livré sans réécrire la plateforme — tout l'intérêt de la couture."""
    remplacant = _ecrire(tmp_path, "default-simple")
    monkeypatch.setenv("CHOREGOS_WORKFLOW_TEMPLATES_DIR", str(tmp_path))

    assert template_path("default-simple") == remplacant
    assert template_names().count("default-simple") == 1, "le nom ne doit pas apparaître deux fois"


def test_plusieurs_repertoires_dans_l_ordre(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    premier, second = tmp_path / "a", tmp_path / "b"
    premier.mkdir()
    second.mkdir()
    _ecrire(premier, "maison")
    _ecrire(second, "maison")
    monkeypatch.setenv("CHOREGOS_WORKFLOW_TEMPLATES_DIR", f"{premier}:{second}")
    assert template_path("maison").parent == premier, "le premier répertoire nommé gagne"


def test_un_nom_inconnu_dit_ce_qui_existe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_WORKFLOW_TEMPLATES_DIR", raising=False)
    with pytest.raises(FileNotFoundError, match="default-simple"):
        template_path("celui-la-n-existe-pas")
