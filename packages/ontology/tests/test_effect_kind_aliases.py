"""The ontology's persisted names are read under both spellings (S45-03, ADR 0098 §4).

Every core action born from the ontology names its effects `ontology.effet` and `ontology.preuve`,
and keeps what the ontology knows of it under `params.ontologie`. Those rows stay as they are; the
English spellings resolve to the same executors and the same knowledge, before any writer switches
(S45-39).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_both_effect_kinds_resolve(plugin: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the ontology extension active, each effect resolves under its English kind and its
    former one, to the same executor, under the same policy."""
    from choregos_api import effets
    from choregos_ontology.service import actions
    from choregos_ontology.service.plugin import register

    # This test's effect registry only: nothing leaks into the next test.
    monkeypatch.setattr(effets, "_EFFETS", dict(effets._EFFETS))
    monkeypatch.setattr(effets, "_POLITIQUES", dict(effets._POLITIQUES))
    register()

    assert (actions.EFFECT_EN, actions.EVIDENCE_EN) == ("ontology.effect", "ontology.evidence")
    assert (actions.EFFECT, actions.EVIDENCE) == ("ontology.effet", "ontology.preuve")  # lang: fr-ok
    assert effets.effet("ontology.effect") is effets.effet(actions.EFFECT) is actions.ontology_effect
    assert effets.effet("ontology.evidence") is effets.effet(actions.EVIDENCE) is actions.ontology_evidence
    for kind in ("ontology.effect", "ontology.evidence", actions.EFFECT, actions.EVIDENCE):
        assert effets.politique_de_l_effet(kind) == "approval", kind


def test_params_read_ontology_then_ontologie() -> None:  # lang: fr-ok - names the persisted key
    from choregos_ontology.service.actions import PARAMS_KEY, _meta

    known = {"action_type": "verify_finding_fixed", "target_ids": ["t-1"]}
    assert PARAMS_KEY == "ontologie"  # lang: fr-ok - the persisted key, never rewritten
    assert _meta(SimpleNamespace(params={"ontologie": known})) == known  # lang: fr-ok
    assert _meta(SimpleNamespace(params={"ontology": known})) == known
    newer = {**known, "target_ids": ["t-2"]}
    assert _meta(SimpleNamespace(params={"ontology": newer, "ontologie": known})) == newer  # lang: fr-ok
    assert _meta(SimpleNamespace(params=None)) == {}
