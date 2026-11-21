"""The step-effect registry: `does:` is a name resolved by the registry; `open_pr` and `merge_pr` are
the `software-delivery` plugin's under their names (ADR 0181, C2); an extension's effect validates, shows
in the process view and requires what it needs (ADR 0181, C0)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from tranibor_contracts import STEP_EFFECTS
from tranibor_core import parse_workflow, step_effects
from tranibor_core.dsl.process import to_process
from tranibor_core.dsl.requirements import requirements
from tranibor_core.step_effects import (
    CORE_EFFECTS,
    ENTERED,
    PASSED,
    PLUGIN_EFFECTS,
    StepEffectRequest,
    forget,
    known_step_effects,
    step_effect,
    step_effect_of,
)

DESK = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: incident-desk, version: 1 }
initial: inbox
actors:
  responder: { type: agent, role: triage, model: "profile:standard" }
  desk: { type: system }
states:
  inbox: { display: To do }
  checked: { display: Checked }
  closed: { display: Closed, terminal: true }
transitions:
  - { id: t-triage, from: inbox, to: checked, by: responder, outputs: [summary] }
  - { id: t-close, from: checked, to: closed, by: desk, does: close_incident }
"""


async def _close(request: StepEffectRequest) -> Mapping[str, Any]:
    return {}


@pytest.fixture
def itsm() -> Iterator[None]:
    step_effect(
        "close_incident",
        gesture="closes the incident",
        when=PASSED,
        plugin="itsm",
        execute=_close,
        needs=("tracker",),
    )
    yield
    forget("itsm")


def test_open_pr_and_merge_pr_are_the_software_delivery_plugin_s_under_their_names() -> None:
    """Registered by its extension since ADR 0181 round C2 (the suite registers it, as the API and the
    worker do); the contract's list still holds them for the window, and the core names their plugin."""
    assert set(CORE_EFFECTS) <= set(known_step_effects())
    opened, merged = step_effect_of("open_pr"), step_effect_of("merge_pr")
    assert opened is not None and merged is not None
    assert (opened.when, merged.when) == (ENTERED, PASSED)
    assert opened.needs == merged.needs == {"scm"}
    assert opened.plugin == merged.plugin == "software-delivery"
    assert STEP_EFFECTS.core == set(CORE_EFFECTS)
    assert STEP_EFFECTS.plugin_of("open_pr") == "software-delivery"
    assert dict.fromkeys(CORE_EFFECTS, "software-delivery") == PLUGIN_EFFECTS
    source = Path(step_effects.__file__).read_text(encoding="utf-8")
    assert "_EFFECTS[" not in source.split("def step_effect(")[0], "the core registers none of its own"


def test_without_its_extension_a_workflow_naming_the_effect_is_refused() -> None:
    from tranibor_core.errors import ValidationError

    with pytest.raises(ValidationError, match="unknown step effect: 'close_incident'"):
        parse_workflow(DESK, strict=False)


def test_with_its_extension_it_validates_shows_and_requires(itsm: None) -> None:
    workflow, report = parse_workflow(DESK, strict=False)
    assert report.valid, [f"{e.code}: {e.message}" for e in report.errors]
    assert STEP_EFFECTS.plugin_of("close_incident") == "itsm"
    steps = {e["to"]: str(e["sentence"]) for e in to_process(workflow)}
    assert "the platform, which closes the incident" in steps["closed"]
    reasons = {r.capability: " ".join(r.reasons) for r in requirements([workflow])}
    assert "t-close closes the incident" in reasons["tracker"]


def test_one_name_is_one_owner(itsm: None) -> None:
    with pytest.raises(ValueError, match="already registered by the plugin `itsm`"):
        step_effect("close_incident", gesture="x", when=PASSED, plugin="other", execute=_close)
    with pytest.raises(ValueError, match="already registered by the plugin `software-delivery`"):
        step_effect("merge_pr", gesture="x", when=PASSED, plugin="other", execute=_close)
    with pytest.raises(ValueError, match="needs an `execute`"):
        step_effect("reopen", gesture="x", when=PASSED, plugin="itsm", execute=None)
    with pytest.raises(ValueError, match="runs `entered` or `passed`"):
        step_effect("reopen", gesture="x", when="later", plugin="itsm", execute=_close)  # type: ignore[arg-type]
