"""La vue « processus » : chaque transition dite en clair (ADR 0031)."""

from __future__ import annotations

import pathlib

from choregos_core import parse_workflow, to_process
from choregos_core.dsl import RESUMES
from choregos_core.gates import known_gates

RACINE = pathlib.Path(__file__).resolve().parents[3]


def _staffing() -> list[dict[str, object]]:
    workflow, _ = parse_workflow((RACINE / "demo/workflows/staffing.yaml").read_text(encoding="utf-8"))
    return to_process(workflow)


def test_chaque_garantie_connue_a_un_resume() -> None:
    """Une garantie nouvelle sans résumé ferait parler la vue processus dans le vide."""
    assert sorted(set(known_gates()) - set(RESUMES)) == []


def test_un_agent_ses_sorties_ses_garanties_et_ses_reprises() -> None:
    sourcing = next(e for e in _staffing() if e["to"] == "sourcing")
    assert sourcing["actor_type"] == "agent"
    phrase = str(sourcing["sentence"])
    assert "the agent `sourceur`" in phrase and "it produced `profils`" in phrase
    assert "the declared outputs are present" in phrase
    assert "retries up to 2 time(s)" in phrase and "«Intervention humaine»" in phrase


def test_une_personne_son_delai_et_le_rejet() -> None:
    validation = next(e for e in _staffing() if e["from"] == "a_valider")
    assert validation["actor_type"] == "human"
    phrase = str(validation["sentence"])
    assert "a person of group `staffing-managers`, within 48 h" in phrase
    assert "If rejected, back to «Sourcing»" in phrase and "times out after 72 h" in phrase


def test_ce_que_fait_la_plateforme_se_dit_en_clair() -> None:
    """`does` (#175) : la vue processus dit que la plateforme ouvre, puis fusionne la PR."""
    from choregos_core.dsl import template_yaml

    workflow, _ = parse_workflow(template_yaml("default-simple"))
    etapes = {e["to"]: str(e["sentence"]) for e in to_process(workflow)}
    assert "the platform, which opens the pull request" in etapes["pr_open"]
    assert "the platform, which merges the pull request" in etapes["merged"]
