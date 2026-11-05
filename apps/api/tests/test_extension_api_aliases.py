# SPDX-License-Identifier: Apache-2.0
"""The extension API in English; the French names it had before 0.21 are gone (S45-04, S45-49).

Their window closed with 0.23.0 (ADR 0098 §3 (f)): a former module no longer imports, a former name
is not an attribute any more, a former keyword is refused. `docs/deployment.md` ("Upgrading to
0.24") lists each one with its replacement. The lines that hold a former name say so.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

M = "choregos_api.extensions"
E = "choregos_api.effects"
Q = "choregos_core.quotas"
A = "choregos_core.admission"
S = "choregos_core.secrets"

#: (module, former name), every one removed in 0.24.0.
FORMER_NAMES: list[tuple[str, str]] = [
    (M, "declarer_un_routeur"),  # lang: fr-ok - removed, asserted gone
    (M, "routeurs_declares"),  # lang: fr-ok - removed, asserted gone
    (M, "reinitialiser"),  # lang: fr-ok - removed, asserted gone
    (M, "declarer_une_section_d_administration"),  # lang: fr-ok - removed, asserted gone
    (M, "sections_declarees"),  # lang: fr-ok - removed, asserted gone
    (M, "chemins_d_une_section"),  # lang: fr-ok - removed, asserted gone
    (M, "FournisseurDOutils"),  # lang: fr-ok - removed, asserted gone
    (M, "declarer_un_fournisseur_d_outils"),  # lang: fr-ok - removed, asserted gone
    (M, "fournisseurs_d_outils"),  # lang: fr-ok - removed, asserted gone
    (M, "FournisseurHumain"),  # lang: fr-ok - removed, asserted gone
    (M, "declarer_des_outils_pour_les_humains"),  # lang: fr-ok - removed, asserted gone
    (M, "fournisseurs_humains"),  # lang: fr-ok - removed, asserted gone
    (M, "declarer_un_installateur_de_gabarit"),  # lang: fr-ok - removed, asserted gone
    (M, "installateurs_de_gabarit"),  # lang: fr-ok - removed, asserted gone
    (M, "GESTES"),  # lang: fr-ok - removed, asserted gone
    (M, "DemandeDeGeste"),  # lang: fr-ok - removed, asserted gone
    (M, "GesteRefuse"),  # lang: fr-ok - removed, asserted gone
    (M, "declarer_un_controle_de_geste"),  # lang: fr-ok - removed, asserted gone
    (M, "controler"),  # lang: fr-ok - removed, asserted gone
    (M, "reinitialiser_les_controles"),  # lang: fr-ok - removed, asserted gone
    (M, "_ROUTEURS"),  # lang: fr-ok - removed, asserted gone
    (M, "_FOURNISSEURS_D_OUTILS"),  # lang: fr-ok - removed, asserted gone
    (M, "_FOURNISSEURS_HUMAINS"),  # lang: fr-ok - removed, asserted gone
    (M, "_INSTALLATEURS"),  # lang: fr-ok - removed, asserted gone
    (M, "_CONTROLES"),  # lang: fr-ok - removed, asserted gone
    (M, "_televersements"),  # lang: fr-ok - removed, asserted gone
    (E, "EffetRefuse"),  # lang: fr-ok - removed, asserted gone
    (E, "ContexteEffet"),  # lang: fr-ok - removed, asserted gone
    (E, "Executer"),  # lang: fr-ok - removed, asserted gone
    (E, "declarer_un_effet"),  # lang: fr-ok - removed, asserted gone
    (E, "politique_de_l_effet"),  # lang: fr-ok - removed, asserted gone
    (E, "effet"),  # lang: fr-ok - removed, asserted gone
    (E, "effets_declares"),  # lang: fr-ok - removed, asserted gone
    (E, "rendre"),  # lang: fr-ok - removed, asserted gone
    (E, "reinitialiser"),  # lang: fr-ok - removed, asserted gone
    (Q, "DEFAUT"),  # lang: fr-ok - removed, asserted gone
    (Q, "Fournisseur"),  # lang: fr-ok - removed, asserted gone
    (Q, "declarer_un_quota"),  # lang: fr-ok - removed, asserted gone
    (Q, "quota_pour"),  # lang: fr-ok - removed, asserted gone
    (Q, "reinitialiser"),  # lang: fr-ok - removed, asserted gone
    (A, "DemandeDeRun"),  # lang: fr-ok - removed, asserted gone
    (A, "AdmissionRefusee"),  # lang: fr-ok - removed, asserted gone
    (A, "Controle"),  # lang: fr-ok - removed, asserted gone
    (A, "declarer_une_admission"),  # lang: fr-ok - removed, asserted gone
    (A, "admissions_declarees"),  # lang: fr-ok - removed, asserted gone
    (A, "verifier"),  # lang: fr-ok - removed, asserted gone
    (A, "reinitialiser"),  # lang: fr-ok - removed, asserted gone
    (S, "SecretIntrouvable"),  # lang: fr-ok - removed, asserted gone
    (S, "declarer_un_resolveur"),  # lang: fr-ok - removed, asserted gone
]


@pytest.mark.parametrize(("module", "former"), FORMER_NAMES)
def test_every_former_extension_name_is_gone(module: str, former: str) -> None:
    assert not hasattr(importlib.import_module(module), former)


@pytest.mark.parametrize(
    "former_module",
    [
        "choregos_api.greffons",  # lang: fr-ok - removed, asserted gone
        "choregos_api.effets",  # lang: fr-ok - removed, asserted gone
    ],
)
def test_the_former_modules_are_gone(former_module: str) -> None:
    with pytest.raises(ImportError):
        importlib.import_module(former_module)


async def _noop(*_args: Any) -> dict[str, Any]:
    return {}


def test_declare_effect_refuses_the_former_policy_keyword() -> None:
    from choregos_api import effects

    effects.reset()
    try:
        with pytest.raises(TypeError):
            effects.declare_effect("x.noop", _noop, **{"politique": "allowed"})  # lang: fr-ok - gone
        assert "x.noop" not in effects.declared_effects()
        effects.declare_effect("x.english", _noop, policy="forbidden")
        assert effects.effect_policy("x.english") == "forbidden"
    finally:
        effects.reset()


def test_the_dsl_value_aliases_are_kept() -> None:
    """`verifier` is a value written in stored workflows, not an extension API name: it stays."""
    from choregos_api import effects

    assert {"check", "verifier", "connector.call"} <= effects.declared_effects()  # lang: fr-ok - DSL value
    assert effects.effect_policy("verifier") == "allowed"  # lang: fr-ok - DSL value


@pytest.mark.parametrize("former", ["interdit", "conflit"])  # lang: fr-ok - removed, asserted gone
def test_gesture_refused_refuses_the_former_natures(former: str) -> None:
    from choregos_api import extensions

    with pytest.raises(ValueError, match="unknown nature"):
        extensions.GestureRefused("no", former)
    assert extensions.GestureRefused("no").nature == "forbidden"
    assert extensions.GestureRefused("no", "conflict").nature == "conflict"
    assert extensions.GestureRefused("no", "reauth").nature == "reauth"
    assert not hasattr(extensions.GestureRefused("no"), "raison")  # lang: fr-ok - removed, asserted gone


def test_the_gesture_request_lost_its_former_properties() -> None:
    from choregos_api import extensions

    request = extensions.GestureRequest("project.create", "varga", None, {"slug": "a"})
    assert (request.gesture, request.target) == ("project.create", {"slug": "a"})
    assert not hasattr(request, "geste")  # lang: fr-ok - removed, asserted gone
    assert not hasattr(request, "cible")  # lang: fr-ok - removed, asserted gone


def test_tool_providers_refuse_the_former_keywords() -> None:
    from choregos_api import extensions

    def list_tools(*_a: Any) -> list[Any]:
        return []

    def call_tool(*_a: Any) -> tuple[int, dict[str, Any]]:
        return 200, {}

    def pending(*_a: Any) -> list[Any]:
        return []

    former_tools: dict[str, Any] = {"lister": list_tools, "appeler": call_tool}  # lang: fr-ok - gone
    extensions.reset()
    try:
        with pytest.raises(TypeError):
            extensions.declare_tool_provider("x", **former_tools)
        with pytest.raises(TypeError):
            extensions.declare_human_tools(
                "h",
                list_tools,
                call_tool,
                **{"en_attente": pending},  # lang: fr-ok - removed, asserted gone
            )
        assert extensions.tool_providers() == {} and extensions.human_tool_providers() == {}

        extensions.declare_tool_provider("x", list_tools, call_tool)
        provider = extensions.tool_providers()["x"]
        assert (provider.list_tools, provider.call_tool) == (list_tools, call_tool)
        # The same declaration again is the same provider.
        extensions.declare_tool_provider("x", list_tools, call_tool)
        for former in ("lister", "appeler"):  # lang: fr-ok - removed, asserted gone
            assert not hasattr(provider, former)

        extensions.declare_human_tools("h", list_tools, call_tool, pending)
        human = extensions.human_tool_providers()["h"]
        assert human.pending is pending
        assert not hasattr(human, "en_attente")  # lang: fr-ok - removed, asserted gone
        assert extensions.HumanToolProvider(list_tools, call_tool).pending is None
        with pytest.raises(TypeError):
            extensions.ToolProvider(list_tools)  # type: ignore[call-arg]
    finally:
        extensions.reset()


def test_quota_refuses_the_former_memory_keyword() -> None:
    from choregos_core.quotas import Quota

    with pytest.raises(TypeError):
        Quota(**{"memoire": "16Gi"})  # lang: fr-ok - removed, asserted gone
    assert not hasattr(Quota(), "memoire")  # lang: fr-ok - removed, asserted gone
    assert Quota(memory="8Gi").memory == "8Gi"
    assert Quota() == Quota("32", "96Gi", 40)
    with pytest.raises(ValueError, match="memory quota"):
        Quota(memory="lots")
    with pytest.raises(ValueError, match="pods"):
        Quota(pods=0)
