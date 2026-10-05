"""Les types de connecteurs déclarent ce qu'ils sont (ADR 0034, S19-01).

Le registre est la seule source : la liste que l'API servait vivait en double, et disait jira
« indisponible » alors qu'il est enregistré. Un secret ne s'écrit qu'en référence, résolue quand
l'adaptateur est construit.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters import (
    AdapterSet,
    configuration_resolue,
    connector_types,
    register,
    spec_of,
    type_par_defaut,
)
from choregos_adapters.registry import _REGISTRY, _SPECS
from choregos_core.secrets import SecretIntrouvable


def test_chaque_type_livre_se_declare_et_aucun_faux_ne_s_affiche() -> None:
    types = {(k, t): spec for k, t, spec in connector_types()}
    assert ("tracker", "jira") in types, "jira est enregistré : il est disponible"
    assert not [t for (_, t) in types if t == "fake"]
    for (kind, type_name), spec in types.items():
        assert kind in spec.capabilities, f"{kind}/{type_name} ne déclare pas sa capacité"
        assert spec.config_schema.get("type") == "object", f"{kind}/{type_name} sans schéma"


def test_les_secrets_connus_sont_declares_comme_tels() -> None:
    """Ce que les fabriques lisent comme un secret (le jeton d'API de Jira, celui d'Argo CD,
    la clef maîtresse de LiteLLM) doit être un champ secret — jamais une clef de `config`."""
    assert "api_token" in spec_of("tracker", "jira").secret_fields  # type: ignore[union-attr]
    assert "token" in spec_of("cd", "argocd").secret_fields  # type: ignore[union-attr]
    assert "master_key" in spec_of("gateway", "litellm").secret_fields  # type: ignore[union-attr]
    for _, _, spec in connector_types():
        assert not set(spec.secret_fields) & set(spec.config_schema.get("properties", {})), spec.display


def test_une_reference_est_resolue_champ_par_champ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_TOKEN", "s3cr3t")
    config = configuration_resolue("tracker", "jira", {"project_key": "RH"}, {"api_token": "env:JIRA_TOKEN"})
    assert config == {"project_key": "RH", "api_token": "s3cr3t"}
    # `secret_ref`, d'avant l'ADR : il vaut pour le premier champ secret du type.
    assert configuration_resolue("tracker", "jira", {}, None, "env:JIRA_TOKEN")["api_token"] == "s3cr3t"


def test_une_reference_irresoluble_arrete_la_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mieux qu'un adaptateur qui retombe en silence sur sa valeur par défaut."""
    monkeypatch.delenv("ARGO_TOKEN", raising=False)
    with pytest.raises(SecretIntrouvable, match="ARGO_TOKEN"):
        configuration_resolue("cd", "argocd", {}, {"token": "env:ARGO_TOKEN"})


def test_les_adaptateurs_d_un_projet_recoivent_leurs_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Par le chemin de l'orchestrateur : `from_connectors` résout avant de construire."""
    sortes = ("tracker", "scm", "ci", "cd", "runtime", "memory", "gateway", "notify")
    recus: dict[str, dict[str, Any]] = {}
    try:
        for sorte in sortes:
            register(sorte, "sonde")(lambda cfg, s=sorte: recus.__setitem__(s, cfg) or object())
        monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
        monkeypatch.setenv("SONDE_TOKEN", "t0k")
        connecteurs: dict[str, dict[str, Any]] = {s: {"type": "sonde"} for s in sortes}
        connecteurs["notify"] = {
            "type": "sonde",
            "config": {"channel": "#rh"},
            "secret_refs": {"bot_token": "env:SONDE_TOKEN"},
        }
        AdapterSet.from_connectors(connecteurs)
    finally:
        for sorte in sortes:
            _REGISTRY.pop((sorte, "sonde"), None)
            _SPECS.pop((sorte, "sonde"), None)
    assert recus["notify"] == {"channel": "#rh", "bot_token": "t0k"}
    assert recus["scm"] == {}


def test_le_type_par_defaut_est_celui_que_prend_un_projet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    assert type_par_defaut("runtime") == "k8s_job"
    assert type_par_defaut("scm") == "github"
    assert type_par_defaut("identity") is None
