"""What the agent reads is in English (ADR 0039, S21-14).

A playbook written in French makes the agent write in French: its specification, its plan, its
review, the text of its PR and its findings — and that is what users read. This guard renders
every role with representative variables — the package's and the ones the demonstration
brings — then reads the invariants, the output contract, the frame of a registry agent, the
`task.md` and the `context.md` the runner writes, and the descriptions of the MCP tools; finally
an AST sweep covers everything the runner and the tool server say (messages to the agent, the
guardrails' reasons, a result's summaries), docstrings aside.

The detector is the one of `tests/langue/test_anglais.py`, loaded by its path: under
`--import-mode=importlib`, a test module does not import another one.
"""

from __future__ import annotations

import importlib.util
import pathlib
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest
from choregos_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    ContextPack,
    MemoryItem,
    ModelRef,
    Permissions,
    PlaybookRef,
    ProjectRef,
    RelatedItem,
    RepoRef,
    StageInput,
    TransitionRef,
    WorkItemRef,
)
from choregos_playbooks import INVARIANTS, KNOWN_ROLES, OUTPUT_CONTRACT, frame, render_playbook

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _guard_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("anglais", ROOT / "tests/langue/test_anglais.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = _guard_module()
looks_like_french: Callable[[str], bool] = GUARD.ressemble_a_du_francais

#: A complete, English context: what the playbook adds, not what the user writes.
CONTEXT = ContextPack(
    query="credit notes",
    memories=[MemoryItem(kind="decision", subject="decision:billing:rounding", content="Round on issue.")],
    incidents=[MemoryItem(kind="incident", subject="incident:billing:2026-09", content="Totals drifted.")],
    related_items=[RelatedItem(key="acme/billing#99", title="Totals ignore discounts", state="done")],
)
VARIABLES: dict[str, Any] = {
    "ticket": {
        "key": "acme/billing#123",
        "title": "Credit notes are not deducted from the total",
        "body": "When an order has a credit note, the displayed total ignores it.",
    },
    "spec": "## Specification\nGiven an order with a credit note…",
    "plan_markdown": "1. Add the computation\n2. Cover it with a test",
    "allowed_paths": ["src/orders/**", "tests/orders/**"],
    "context": CONTEXT,
}


def _french_lines(text: str) -> list[str]:
    """Line by line, removing nothing: a JSON example or a code excerpt is read too."""
    return [line.strip()[:100] for line in text.splitlines() if looks_like_french(line)]


def _stage_input() -> StageInput:
    return StageInput(
        run_id="run-1",
        attempt=1,
        project=ProjectRef(slug="billing", org="acme"),
        work_item=WorkItemRef(
            key="acme/billing#123",
            title="Credit notes are not deducted from the total",
            body="When an order has a credit note, the displayed total ignores it.",
        ),
        transition=TransitionRef(id="t-implement", role="implement", **{"from": "ready"}, to="implemented"),
        repo=RepoRef(url="https://example.com/acme/billing.git", base_branch="main", work_branch="w/123"),
        agent=AgentRef(backend="claude-code"),
        model=ModelRef(
            litellm_model="platform/standard", base_url="http://gateway:4000", api_format="openai"
        ),
        budget=Budget(usd=5, max_turns=20, max_minutes=30),
        allowed_paths=["src/orders/**", "tests/orders/**"],
        playbook=PlaybookRef(ref="implement@1"),
        permissions=Permissions(write_paths=["src/orders/**", "tests/orders/**"]),
        callbacks=Callbacks(api_url="http://localhost:0/internal", run_token="tok"),
    )


@pytest.mark.parametrize("role", KNOWN_ROLES)
def test_every_rendered_playbook_is_in_english(role: str) -> None:
    rendered = render_playbook(role, **VARIABLES)
    assert "Round on issue." in rendered and "Totals ignore discounts" in rendered, "memory is rendered"
    faults = _french_lines(rendered)
    assert not faults, f"French in the `{role}` playbook:\n  " + "\n  ".join(faults)


@pytest.mark.parametrize("role", ["sourcing", "qualification"])
def test_the_demonstration_playbooks_are_in_english(role: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Brought by the deployment (`demo/playbooks`, mounted through `CHOREGOS_PLAYBOOKS_DIR`):
    rendered by the same path as in production. The Jinja loader caches its directories."""
    from choregos_playbooks import _env

    monkeypatch.setenv("CHOREGOS_PLAYBOOKS_DIR", str(ROOT / "demo/playbooks"))
    _env.cache_clear()
    try:
        rendered = render_playbook(role, **VARIABLES, inputs={"profils": "- Alice, senior Python developer"})
    finally:
        monkeypatch.delenv("CHOREGOS_PLAYBOOKS_DIR")
        _env.cache_clear()
    assert "acme/billing#123" in rendered and "## Invariants" in rendered
    faults = _french_lines(rendered)
    assert not faults, f"French in the demonstration playbook `{role}`:\n  " + "\n  ".join(faults)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        pytest.param("INVARIANTS", INVARIANTS, id="invariants"),
        pytest.param("OUTPUT_CONTRACT", OUTPUT_CONTRACT, id="output-contract"),
        pytest.param("frame", frame("Do the work described above."), id="registry-agent-frame"),
    ],
)
def test_the_invariants_the_contract_and_the_frame_are_in_english(name: str, text: str) -> None:
    faults = _french_lines(text)
    assert not faults, f"French in {name}:\n  " + "\n  ".join(faults)


OUTPUT_LANGUAGE = "Write in English everything a person will read"


@pytest.mark.parametrize("role", sorted(KNOWN_ROLES))
def test_every_role_asks_to_write_in_english(role: str) -> None:
    # An agent writes in the language of what it reads: a ticket with French fields (a person's
    # name, a street address) made an otherwise all-English agent write a plan in French (08/10).
    assert OUTPUT_LANGUAGE in render_playbook(role, **VARIABLES)


def test_a_registry_agent_gets_the_language_instruction_in_its_frame() -> None:
    assert OUTPUT_LANGUAGE in frame("Prepare the onboarding plan.")


def test_the_runners_task_md_and_context_md_are_in_english() -> None:
    from choregos_runner.workspace import _context_markdown, _task_markdown

    stage_input = _stage_input()
    for name, text in (
        ("task.md", _task_markdown(stage_input)),
        ("context.md", _context_markdown(CONTEXT)),
        ("context.md (empty)", _context_markdown(None)),
    ):
        assert "Credit notes" in text or "Round on issue." in text or "No memory" in text, name
        faults = _french_lines(text)
        assert not faults, f"French in {name}:\n  " + "\n  ".join(faults)


def _descriptions(value: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, sub_value in value.items():
            if key == "description" and isinstance(sub_value, str):
                yield path, sub_value
            else:
                yield from _descriptions(sub_value, f"{path}.{key}")
    elif isinstance(value, list):
        for element in value:
            yield from _descriptions(element, path)


def test_the_mcp_tool_descriptions_are_in_english() -> None:
    from choregos_tools_mcp.tools import TOOL_SCHEMAS

    descriptions = [d for tool in TOOL_SCHEMAS for d in _descriptions(tool, str(tool["name"]))]
    assert len(descriptions) >= len(TOOL_SCHEMAS), "every tool describes itself"
    faults = [f"{path}: {text[:90]!r}" for path, text in descriptions if looks_like_french(text)]
    assert not faults, "French in the agent's tools:\n  " + "\n  ".join(faults)


@pytest.mark.parametrize(
    "root",
    ["packages/runner/src/choregos_runner", "packages/tools-mcp/src/choregos_tools_mcp"],
    ids=["runner", "mcp-tools"],
)
def test_what_the_runner_and_the_tools_say_is_in_english(root: str) -> None:
    """Messages to the agent, the guardrails' reasons (also read in the access sheet), a result's
    summaries, errors a console shows: every string outside docstrings."""
    faults = GUARD._francais_d_un_paquet(root)
    assert not faults, "French in what the package says:\n  " + "\n  ".join(faults)
