# SPDX-License-Identifier: Apache-2.0
"""The extension API in English, and the French names it had before 0.21 (S45-04, ADR 0098 §3).

Every former name keeps resolving to the English object until 0.23.0 and warns. The pairs below
are the public rename table of `docs/development.md` ("Former names"); the lines that hold a former
name carry the alias marker.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from httpx import AsyncClient

M = "choregos_api.extensions"
E = "choregos_api.effects"
Q = "choregos_core.quotas"
A = "choregos_core.admission"
S = "choregos_core.secrets"

#: (former module, English module, former name, English name)
PAIRS: list[tuple[str, str, str, str]] = [
    ("choregos_api.greffons", M, "declarer_un_routeur", "declare_router"),  # lang: fr-ok alias:external
    ("choregos_api.greffons", M, "routeurs_declares", "declared_routers"),  # lang: fr-ok alias:external
    ("choregos_api.greffons", M, "reinitialiser", "reset"),  # lang: fr-ok alias:external
    (
        "choregos_api.greffons",
        M,
        "declarer_une_section_d_administration",  # lang: fr-ok alias:external
        "declare_admin_section",
    ),
    ("choregos_api.greffons", M, "sections_declarees", "declared_sections"),  # lang: fr-ok alias:external
    ("choregos_api.greffons", M, "chemins_d_une_section", "section_paths"),  # lang: fr-ok alias:external
    ("choregos_api.greffons", M, "FournisseurDOutils", "ToolProvider"),  # lang: fr-ok alias:external
    (
        "choregos_api.greffons",
        M,
        "declarer_un_fournisseur_d_outils",  # lang: fr-ok alias:external
        "declare_tool_provider",
    ),
    ("choregos_api.greffons", M, "fournisseurs_d_outils", "tool_providers"),  # lang: fr-ok alias:external
    ("choregos_api.greffons", M, "FournisseurHumain", "HumanToolProvider"),  # lang: fr-ok alias:external
    (
        "choregos_api.greffons",
        M,
        "declarer_des_outils_pour_les_humains",  # lang: fr-ok alias:external
        "declare_human_tools",
    ),
    (
        "choregos_api.greffons",
        M,
        "fournisseurs_humains",
        "human_tool_providers",
    ),  # lang: fr-ok alias:external
    (
        "choregos_api.greffons",
        M,
        "declarer_un_installateur_de_gabarit",  # lang: fr-ok alias:external
        "declare_template_installer",
    ),
    (
        "choregos_api.greffons",
        M,
        "installateurs_de_gabarit",  # lang: fr-ok alias:external
        "template_installers",
    ),
    ("choregos_api.greffons", M, "GESTES", "GESTURES"),  # lang: fr-ok alias:external
    ("choregos_api.greffons", M, "DemandeDeGeste", "GestureRequest"),  # lang: fr-ok alias:external
    ("choregos_api.greffons", M, "GesteRefuse", "GestureRefused"),  # lang: fr-ok alias:external
    (
        "choregos_api.greffons",
        M,
        "declarer_un_controle_de_geste",  # lang: fr-ok alias:external
        "declare_gesture_check",
    ),
    ("choregos_api.greffons", M, "controler", "check_gesture"),  # lang: fr-ok alias:external
    (
        "choregos_api.greffons",
        M,
        "reinitialiser_les_controles",  # lang: fr-ok alias:external
        "reset_gesture_checks",
    ),
    ("choregos_api.effets", E, "EffetRefuse", "EffectRefused"),  # lang: fr-ok alias:external
    ("choregos_api.effets", E, "ContexteEffet", "EffectContext"),  # lang: fr-ok alias:external
    ("choregos_api.effets", E, "Executer", "Execute"),  # lang: fr-ok alias:external
    ("choregos_api.effets", E, "declarer_un_effet", "declare_effect"),  # lang: fr-ok alias:external
    ("choregos_api.effets", E, "politique_de_l_effet", "effect_policy"),  # lang: fr-ok alias:external
    ("choregos_api.effets", E, "effet", "effect"),  # lang: fr-ok alias:external
    ("choregos_api.effets", E, "effets_declares", "declared_effects"),  # lang: fr-ok alias:external
    ("choregos_api.effets", E, "rendre", "render"),  # lang: fr-ok alias:external
    ("choregos_api.effets", E, "reinitialiser", "reset"),  # lang: fr-ok alias:external
    (Q, Q, "DEFAUT", "DEFAULT"),  # lang: fr-ok alias:external
    (Q, Q, "Fournisseur", "QuotaProvider"),  # lang: fr-ok alias:external
    (Q, Q, "declarer_un_quota", "declare_quota"),  # lang: fr-ok alias:external
    (Q, Q, "quota_pour", "quota_for"),  # lang: fr-ok alias:external
    (Q, Q, "reinitialiser", "reset"),  # lang: fr-ok alias:external
    (A, A, "DemandeDeRun", "RunRequest"),  # lang: fr-ok alias:external
    (A, A, "AdmissionRefusee", "AdmissionRefused"),  # lang: fr-ok alias:external
    (A, A, "Controle", "AdmissionCheck"),  # lang: fr-ok alias:external
    (A, A, "declarer_une_admission", "declare_admission"),  # lang: fr-ok alias:external
    (A, A, "admissions_declarees", "declared_admissions"),  # lang: fr-ok alias:external
    (A, A, "verifier", "check"),  # lang: fr-ok alias:external
    (A, A, "reinitialiser", "reset"),  # lang: fr-ok alias:external
    (S, S, "SecretIntrouvable", "SecretNotFound"),  # lang: fr-ok alias:external
    (S, S, "declarer_un_resolveur", "declare_resolver"),  # lang: fr-ok alias:external
]


@pytest.mark.parametrize(("former_module", "module", "old", "new"), PAIRS)
def test_every_former_extension_name_is_the_english_object(
    former_module: str, module: str, old: str, new: str
) -> None:
    former = importlib.import_module(former_module)
    current = importlib.import_module(module)
    with pytest.warns(DeprecationWarning, match="removed in 0.23.0"):
        found = getattr(former, old)
    assert found is getattr(current, new)


@pytest.mark.parametrize(
    ("former_module", "module"),
    [
        ("choregos_api.greffons", "choregos_api.extensions"),  # lang: fr-ok alias:external
        ("choregos_api.effets", "choregos_api.effects"),  # lang: fr-ok alias:external
    ],
)
def test_the_former_modules_are_the_new_ones(former_module: str, module: str) -> None:
    assert importlib.import_module(former_module) is importlib.import_module(module)
    package = importlib.import_module("choregos_api")
    assert getattr(package, former_module.rsplit(".", 1)[1]) is importlib.import_module(module)


def test_patching_a_former_private_name_patches_the_english_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api import extensions, greffons

    monkeypatch.setattr(greffons, "_INSTALLATEURS", {})  # lang: fr-ok alias:internal
    assert extensions.template_installers() == {}
    extensions.declare_template_installer("x", object())
    assert "x" in extensions._INSTALLERS
    monkeypatch.undo()
    assert "x" not in extensions.template_installers()


async def _noop(*_args: Any) -> dict[str, Any]:
    return {}


def test_declare_effect_accepts_the_former_policy_keyword() -> None:
    from choregos_api import effects, effets

    effects.reset()
    try:
        with pytest.warns(DeprecationWarning, match="politique"):
            effets.declarer_un_effet("x.noop", _noop, politique="allowed")  # lang: fr-ok alias:external
        assert effects.effect_policy("x.noop") == "allowed"
        effects.declare_effect("x.english", _noop, policy="forbidden")
        assert effects.effect_policy("x.english") == "forbidden"
    finally:
        effects.reset()


def test_the_dsl_value_aliases_are_kept() -> None:
    from choregos_api import effects

    assert {"check", "verifier", "connector.call"} <= effects.declared_effects()  # lang: fr-ok alias:external
    assert effects.effect_policy("verifier") == "allowed"  # lang: fr-ok alias:external


def test_gesture_refused_accepts_former_natures() -> None:
    from choregos_api import extensions, greffons

    with pytest.warns(DeprecationWarning, match="conflict"):
        refusal = greffons.GesteRefuse("no", "conflit")  # lang: fr-ok alias:external
    assert refusal.nature == "conflict"
    assert refusal.raison == "no"  # lang: fr-ok alias:external
    with pytest.warns(DeprecationWarning, match="forbidden"):
        assert extensions.GestureRefused("no", "interdit").nature == "forbidden"  # lang: fr-ok alias:external
    assert extensions.GestureRefused("no").nature == "forbidden"
    assert extensions.GestureRefused("no", "reauth").nature == "reauth"
    with pytest.raises(ValueError, match="unknown nature"):
        extensions.GestureRefused("no", "maybe")


async def test_a_former_nature_still_answers_409(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api import extensions, greffons

    def ceiling(_session: Any, _request: Any) -> None:
        raise greffons.GesteRefuse("full", "conflit")  # lang: fr-ok alias:external

    extensions.declare_gesture_check("project.create", "ceiling", ceiling)
    try:
        body = {
            "slug": "second",
            "name": "second",
            "config": {
                "slug": "second",
                "org": "varga",
                "repo": {
                    "url": "https://github.com/varga/second.git",
                    "default_branch": "main",
                    "language": "python",
                },
            },
        }
        refused = await client.post("/api/v1/orgs/varga/projects", json=body)
        assert refused.status_code == 409, refused.text
        assert "ceiling: full" in refused.text
    finally:
        extensions.reset_gesture_checks()


def test_the_gesture_request_has_its_former_properties() -> None:
    from choregos_api import extensions

    request = extensions.GestureRequest("project.create", "varga", None, {"slug": "a"})
    assert (request.gesture, request.target) == ("project.create", {"slug": "a"})
    assert (request.geste, request.cible) == ("project.create", {"slug": "a"})  # lang: fr-ok alias:external


def test_tool_providers_accept_former_keywords() -> None:
    from choregos_api import extensions, greffons

    def list_tools(*_a: Any) -> list[Any]:
        return []

    def call_tool(*_a: Any) -> tuple[int, dict[str, Any]]:
        return 200, {}

    def pending(*_a: Any) -> list[Any]:
        return []

    extensions.reset()
    try:
        with pytest.warns(DeprecationWarning, match="lister"):  # lang: fr-ok alias:external
            greffons.declarer_un_fournisseur_d_outils(
                "x", lister=list_tools, appeler=call_tool
            )  # lang: fr-ok alias:external
        provider = extensions.tool_providers()["x"]
        assert provider.list_tools is list_tools
        assert provider.call_tool is call_tool
        assert provider.lister is list_tools  # lang: fr-ok alias:external
        assert provider.appeler is call_tool  # lang: fr-ok alias:external
        # The same declaration, in English, is the same provider.
        extensions.declare_tool_provider("x", list_tools, call_tool)

        with pytest.warns(DeprecationWarning, match="en_attente"):  # lang: fr-ok alias:external
            greffons.declarer_des_outils_pour_les_humains(  # lang: fr-ok alias:external
                "h",
                list_tools,
                call_tool,
                en_attente=pending,  # lang: fr-ok alias:external
            )
        human = extensions.human_tool_providers()["h"]
        assert human.pending is pending
        assert human.en_attente is pending  # lang: fr-ok alias:external
        assert extensions.HumanToolProvider(list_tools, call_tool).pending is None
        with pytest.raises(TypeError):
            extensions.ToolProvider(list_tools)
    finally:
        extensions.reset()


def test_quota_accepts_the_former_memory_keyword() -> None:
    from choregos_core.quotas import Quota

    with pytest.warns(DeprecationWarning, match="memoire"):  # lang: fr-ok alias:external
        quota = Quota(memoire="16Gi")  # lang: fr-ok alias:external
    assert quota.memory == "16Gi" == quota.memoire  # lang: fr-ok alias:external
    assert Quota(memory="8Gi").memory == "8Gi"
    assert Quota() == Quota("32", "96Gi", 40)
    with pytest.raises(ValueError, match="memory quota"):
        Quota(memory="lots")
    with pytest.raises(ValueError, match="pods"):
        Quota(pods=0)
