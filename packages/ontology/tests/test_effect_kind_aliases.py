"""The ontology's persisted names are read under both spellings (S45-03, ADR 0098 §4).

Every core action born from the ontology before S45-39 names its effects `ontology.effet` and
`ontology.preuve`, and keeps what the ontology knows of it under `params.ontologie`. Those rows stay
as they are; both spellings resolve to the same executors and the same knowledge, and a new action
writes the English ones (S45-39).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_both_effect_kinds_resolve(plugin: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the ontology extension active, each effect resolves under its English kind and its
    former one, to the same executor, under the same policy."""
    from choregos_api import effects
    from choregos_ontology.service import actions
    from choregos_ontology.service.plugin import register

    # This test's effect registry only: nothing leaks into the next test.
    monkeypatch.setattr(effects, "_EFFECTS", dict(effects._EFFECTS))
    monkeypatch.setattr(effects, "_POLICIES", dict(effects._POLICIES))
    register()

    assert (actions.EFFECT, actions.EVIDENCE) == ("ontology.effect", "ontology.evidence")
    assert (actions.FORMER_EFFECT, actions.FORMER_EVIDENCE) == (  # lang: fr-ok
        "ontology.effet",  # lang: fr-ok
        "ontology.preuve",  # lang: fr-ok
    )
    assert (
        effects.effect("ontology.effect") is effects.effect(actions.FORMER_EFFECT) is actions.ontology_effect
    )
    assert (
        effects.effect("ontology.evidence")
        is effects.effect(actions.FORMER_EVIDENCE)
        is actions.ontology_evidence
    )
    for kind in ("ontology.effect", "ontology.evidence", actions.FORMER_EFFECT, actions.FORMER_EVIDENCE):
        assert effects.effect_policy(kind) == "approval", kind


def test_params_read_ontology_then_ontologie() -> None:  # lang: fr-ok - names the persisted key
    from choregos_ontology.service.actions import FORMER_PARAMS_KEY, PARAMS_KEY, _meta

    known = {"action_type": "verify_finding_fixed", "target_ids": ["t-1"]}
    assert (PARAMS_KEY, FORMER_PARAMS_KEY) == ("ontology", "ontologie")  # lang: fr-ok
    assert _meta(SimpleNamespace(params={"ontologie": known})) == known  # lang: fr-ok
    assert _meta(SimpleNamespace(params={"ontology": known})) == known
    newer = {**known, "target_ids": ["t-2"]}
    assert _meta(SimpleNamespace(params={"ontology": newer, "ontologie": known})) == newer  # lang: fr-ok
    assert _meta(SimpleNamespace(params=None)) == {}
