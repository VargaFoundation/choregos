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
    assert "pas un ordre" in rendered or "pas des instructions" in rendered
    assert "Une décision antérieure." in rendered


def test_role_inconnu_est_refuse() -> None:
    with pytest.raises(FileNotFoundError, match="playbook inconnu"):
        render_playbook("licorne")


@pytest.mark.parametrize("path", sorted(CASES_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_les_evals_de_playbooks_passent(path) -> None:
    result = evaluate_case(path)
    assert result.passed, f"manque : {result.missing} · interdit : {result.forbidden}"
