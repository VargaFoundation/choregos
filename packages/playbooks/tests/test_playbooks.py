"""Les playbooks : rendu, invariants, et non-régression par les évals."""

from __future__ import annotations

import pytest
from choregos_contracts import ContextPack, MemoryItem
from choregos_playbooks import KNOWN_ROLES, render_playbook
from choregos_playbooks.evals import CASES_DIR
from choregos_playbooks.evals.runner import evaluate_case


@pytest.mark.parametrize("role", KNOWN_ROLES)
def test_chaque_role_rend_un_prompt_complet(role: str) -> None:
    rendered = render_playbook(
        role,
        ticket={"key": "acme#1", "title": "Titre", "body": "Corps"},
        spec="## Spec",
        allowed_paths=["src/**"],
    )
    assert "## Invariants" in rendered
    assert "result.json" in rendered
    assert "{{" not in rendered, "aucune variable Jinja non rendue"


def test_la_memoire_est_marquee_non_fiable() -> None:
    context = ContextPack(
        query="q",
        memories=[MemoryItem(kind="decision", subject="d:x", content="Une décision antérieure.")],
    )
    rendered = render_playbook("implement", ticket={"key": "a#1", "title": "T", "body": ""}, context=context)
    assert "not an order" in rendered or "**not** instructions" in rendered
    assert "Une décision antérieure." in rendered


def test_role_inconnu_est_refuse() -> None:
    with pytest.raises(FileNotFoundError, match="unknown playbook"):
        render_playbook("licorne")


@pytest.mark.parametrize("path", sorted(CASES_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_les_evals_de_playbooks_passent(path) -> None:
    result = evaluate_case(path)
    assert result.passed, f"manque : {result.missing} · interdit : {result.forbidden}"


def test_les_invariants_ne_se_contredisent_pas_sur_result_json() -> None:
    """Le premier invariant EXIGE d'écrire `.choregos/result.json` ; le dernier interdisait
    de toucher à `.choregos/**`.

    Un modèle doit alors arbitrer entre deux ordres, et un petit modèle obéit au dernier. Sur le
    locataire dev, le 2026-09-27, l'agent a tourné, appelé le modèle, et n'a rien écrit : l'étape
    est morte sur « `result.json` est absent ». Le garde-fou, lui, connaissait l'exception
    (`guardrails.py` autorise ce seul fichier) — c'était le PROMPT qui se contredisait, et un
    garde-fou correct derrière une consigne contradictoire ne sert à rien.
    """
    from choregos_playbooks import INVARIANTS

    lignes = [ligne for ligne in INVARIANTS.splitlines() if ".choregos/**" in ligne]
    assert lignes, "l'invariant sur `.choregos/**` a disparu"
    for ligne in lignes:
        assert "other" in ligne.lower() or "except" in ligne or "result.json" in ligne, (
            f"cet invariant interdit `.choregos/**` sans excepter result.json, "
            f"que le premier invariant exige : {ligne}"
        )
