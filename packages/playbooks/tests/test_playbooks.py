"""Playbooks: rendering, invariants, and non-regression through the evals."""

from __future__ import annotations

import pytest
from choregos_contracts import ContextPack, MemoryItem
from choregos_playbooks import KNOWN_ROLES, render_playbook
from choregos_playbooks.evals import CASES_DIR
from choregos_playbooks.evals.runner import evaluate_case


@pytest.mark.parametrize("role", KNOWN_ROLES)
def test_every_role_renders_a_complete_prompt(role: str) -> None:
    rendered = render_playbook(
        role,
        ticket={"key": "acme#1", "title": "Title", "body": "Body"},
        spec="## Spec",
        allowed_paths=["src/**"],
    )
    assert "## Invariants" in rendered
    assert "result.json" in rendered
    assert "{{" not in rendered, "no Jinja variable left unrendered"


def test_memory_is_marked_untrusted() -> None:
    context = ContextPack(
        query="q",
        memories=[MemoryItem(kind="decision", subject="d:x", content="An earlier decision.")],
    )
    rendered = render_playbook("implement", ticket={"key": "a#1", "title": "T", "body": ""}, context=context)
    assert "not an order" in rendered or "**not** instructions" in rendered
    assert "An earlier decision." in rendered


def test_unknown_role_is_refused() -> None:
    with pytest.raises(FileNotFoundError, match="unknown playbook"):
        render_playbook("unicorn")


@pytest.mark.parametrize("path", sorted(CASES_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_the_playbook_evals_pass(path) -> None:
    result = evaluate_case(path)
    assert result.passed, f"missing: {result.missing} · forbidden: {result.forbidden}"


def test_the_invariants_do_not_contradict_each_other_on_result_json() -> None:
    """The first invariant REQUIRES writing `.choregos/result.json`; the last one used to forbid
    touching `.choregos/**`.

    A model then has to arbitrate between two orders, and a small model obeys the last one. On the
    dev tenant, on 2026-09-27, the agent ran, called the model, and wrote nothing: the stage died
    on "`result.json` is missing". The guardrail knew the exception (`guardrails.py` allows that
    one file) — it was the PROMPT that contradicted itself, and a correct guardrail behind a
    contradictory instruction is useless.
    """
    from choregos_playbooks import INVARIANTS

    lines = [line for line in INVARIANTS.splitlines() if ".choregos/**" in line]
    assert lines, "the invariant on `.choregos/**` is gone"
    for line in lines:
        assert "other" in line.lower() or "except" in line or "result.json" in line, (
            f"this invariant forbids `.choregos/**` without excepting result.json, "
            f"which the first invariant requires: {line}"
        )
