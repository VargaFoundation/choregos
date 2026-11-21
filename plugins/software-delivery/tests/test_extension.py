"""The twelve gates of software delivery are the `software-delivery` plugin's extension (ADR 0181, round C2).

The extension is the workspace distribution `tranibor-software-delivery`, loaded from its `choregos.plugins`
entry point by the API and the worker. Without it the core knows the twelve only by name, so a workflow
naming one is refused at publication, naming the plugin; with it, they validate and are judged on the
facts of its `delivery` provider.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path

import pytest
from tranibor_core import parse_workflow
from tranibor_core.dsl import template_yaml
from tranibor_core.gates import (
    PLUGIN_GATES,
    GateContext,
    evaluate,
    forget,
    gate_plugin,
    gate_reads,
    is_run_scoped,
    known_gates,
    known_providers,
    reads_the_workspace,
    unavailable,
)
from tranibor_software_delivery import NAME
from tranibor_software_delivery.extension import register
from tranibor_software_delivery.gates import (
    DIFF_GATES,
    GATES,
    PROVIDER,
    RUN_SCOPED_GATES,
    DeliveryFacts,
    delivery_context,
)

ROOT = Path(__file__).resolve().parents[3]
CORE_GATES = Path(ROOT, "packages/core/src/tranibor_core/gates/__init__.py")
WORKFLOW = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: delivery, version: 1}
actors:
  dev: {type: agent, role: implement}
states:
  todo: {display: To do, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-do, from: todo, to: done, by: dev, gates: [{name: GATE}]}
"""


@pytest.fixture
def without_the_extension() -> Iterator[None]:
    """The deployment without `tranibor-software-delivery`: its gates and provider forgotten, then back."""
    forget(NAME)
    try:
        yield
    finally:
        register()


def test_the_entry_point_is_declared_under_the_plugin_s_name() -> None:
    points = {p.name: p.value for p in metadata.entry_points(group="choregos.plugins")}
    assert points[NAME] == "tranibor_software_delivery.extension:register"
    assert NAME == "software-delivery"


def test_the_twelve_are_the_plugin_s_and_the_core_registers_none_of_them() -> None:
    assert set(GATES) == set(PLUGIN_GATES) and len(GATES) == 12
    for name in GATES:
        assert gate_plugin(name) == NAME, name
    source = CORE_GATES.read_text(encoding="utf-8")
    assert not [name for name in GATES if f'@gate("{name}"' in source or f"def _{name}(" in source]
    assert PROVIDER in known_providers()
    assert {name for name in GATES if gate_reads(name)} == set(GATES) - {"coverage_delta_min"}
    assert {name for name in GATES if reads_the_workspace(name)} == DIFF_GATES
    assert {name for name in GATES if is_run_scoped(name)} == RUN_SCOPED_GATES


def test_without_the_extension_a_workflow_naming_one_is_refused_naming_the_plugin(
    without_the_extension: None,
) -> None:
    for name in GATES:
        assert name not in known_gates()
        why = unavailable(name)
        assert why is not None and f"plugin `{NAME}`" in why, name
        _, report = parse_workflow(WORKFLOW.replace("GATE", name), strict=False)
        [unknown] = [e for e in report.errors if e.code == "gate.unknown"]
        assert NAME in unknown.message, name
    # A shipped template of software delivery is refused as a whole, gate by gate.
    _, report = parse_workflow(template_yaml("default-simple"), strict=False)
    assert report.errors and all(NAME in e.message for e in report.errors if e.code == "gate.unknown")


def test_with_the_extension_they_validate_and_are_judged_on_the_provider_s_facts() -> None:
    for name in GATES:
        _, report = parse_workflow(
            WORKFLOW.replace("GATE", f"{name}, params: {{paths: [docs/**]}}"), strict=False
        )
        assert not [e for e in report.errors if e.code in {"gate.unknown", "gate.provider_missing"}], name
    # What a gate cannot do without is the plugin's to say, and the validator holds it.
    _, report = parse_workflow(WORKFLOW.replace("GATE", "markdown_sections"), strict=False)
    assert [e.code for e in report.errors] == ["gate.nothing_to_check"]
    seen = DeliveryFacts(changed_files=["src/a.py", "docs/x.md"], allowed_paths=["src/**"])
    assert not evaluate("scope_respected", delivery_context(seen)).passed
    assert evaluate("scope_respected", delivery_context(DeliveryFacts(changed_files=["src/a.py"]))).passed


def test_a_gate_whose_provider_failed_refuses_rather_than_judge_nothing() -> None:
    """No `delivery` key: the provider raised. The diff gates say the diff is unavailable, the pull
    request's wait, `already_fixed_proven` refuses."""
    unread = GateContext()
    for name in DIFF_GATES:
        verdict = evaluate(name, unread, {"paths": ["docs/**"]})
        assert verdict.blocking and "diff unavailable" in verdict.detail, name
    assert evaluate("ci_green", unread).pending


def test_a_run_s_writes_are_read_by_a_run_scoped_declaration_only() -> None:
    """What the provider read of the last run (`run_changed_files`) is what `scope_respected` holds;
    with `over: work_item`, the whole diff is."""
    facts = DeliveryFacts(changed_files=["Makefile", "src/a.py"], run_changed_files=["src/a.py"])
    context = delivery_context(facts)
    assert evaluate("scope_respected", context, {"paths": ["src/**"]}).passed
    whole = evaluate("scope_respected", context, {"over": "work_item", "paths": ["src/**"]})
    assert whole.blocking and whole.annotations == ["Makefile"]


def test_the_policy_s_diff_bounds_hold_when_a_declaration_names_none() -> None:
    facts = DeliveryFacts(changed_files=["a", "b", "c"], additions=10, max_diff=(2, 1000))
    assert evaluate("diff_size_max", delivery_context(facts)).blocking
    assert evaluate("diff_size_max", delivery_context(facts), {"files": 5}).passed


def test_the_distribution_is_built_released_and_installed_in_the_images() -> None:
    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "plugins/software-delivery" in root["tool"]["uv"]["workspace"]["members"]
    assert "tranibor-software-delivery" in root["project"]["dependencies"]
    assert "plugins/software-delivery/src" in root["tool"]["coverage"]["run"]["source"]
    assert "plugins/software-delivery/src" in root["tool"]["mypy"]["mypy_path"]
    own = tomllib.loads((ROOT / "plugins/software-delivery/pyproject.toml").read_text(encoding="utf-8"))
    assert own["project"]["name"] == "tranibor-software-delivery"
    assert own["project"]["version"] == root["project"]["version"], "released with the core"
    # The API and the worker install the code; the other images read the member's metadata.
    api = (ROOT / "docker/api.Dockerfile").read_text(encoding="utf-8")
    assert "COPY plugins/software-delivery plugins/software-delivery" in api
    for image in ("runner", "outpost", "tools"):
        text = (ROOT / f"docker/{image}.Dockerfile").read_text(encoding="utf-8")
        assert "COPY plugins/software-delivery/pyproject.toml plugins/software-delivery/" in text, image
    # Its wheel ships with the release, as every workspace package's.
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "uv build --all-packages --out-dir dist" in release
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "- 'plugins/software-delivery/**'" in ci


# ───────────────────────── the step effects (S83-04b) ─────────────────────────

DOES = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: delivery, version: 1}
actors:
  platform: {type: system}
states:
  todo: {display: To do, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-do, from: todo, to: done, by: platform, does: EFFECT}
"""


@pytest.fixture
def without_the_effects() -> Iterator[None]:
    from tranibor_core import step_effects

    step_effects.forget(NAME)
    try:
        yield
    finally:
        register()


def test_open_pr_and_merge_pr_are_registered_by_the_plugin() -> None:
    from tranibor_contracts import STEP_EFFECTS
    from tranibor_core.step_effects import ENTERED, PASSED, step_effect_of
    from tranibor_software_delivery.effects import EFFECTS

    assert set(EFFECTS) == {"open_pr", "merge_pr"}
    for name, when in (("open_pr", ENTERED), ("merge_pr", PASSED)):
        found = step_effect_of(name)
        assert found is not None and found.plugin == NAME and found.when == when, name
        assert found.needs == {"scm"}
        assert STEP_EFFECTS.plugin_of(name) == NAME
        _, report = parse_workflow(DOES.replace("EFFECT", name), strict=False)
        assert not [e for e in report.errors if e.code == "transition.effect_unavailable"], name


def test_without_the_extension_does_naming_one_is_refused_naming_the_plugin(
    without_the_effects: None,
) -> None:
    from tranibor_core.dsl import load_template
    from tranibor_core.dsl.validator import deployment_issues

    for name in ("open_pr", "merge_pr"):
        workflow, report = parse_workflow(DOES.replace("EFFECT", name), strict=False)
        [refused] = [e for e in report.errors if e.code == "transition.effect_unavailable"]
        assert f"plugin `{NAME}`" in refused.message and f"`{name}`" in refused.message
        elsewhere, others = deployment_issues(workflow, report)
        assert refused in elsewhere and refused not in others, "left to the deployment offline"
    # A state's name that still implies the effect (`merged`, #175) is refused the same way.
    implied = DOES.replace(", does: EFFECT", "").replace("done:", "merged:").replace("to: done", "to: merged")
    _, report = parse_workflow(implied, strict=False)
    assert "transition.effect_unavailable" in {e.code for e in report.errors}
    # A shipped template still loads where the extension is absent: the deployment refuses it.
    load_template.cache_clear()
    assert load_template("default-simple").transitions
    load_template.cache_clear()


def test_the_plugin_declares_its_values_in_the_contracts_open_lists() -> None:
    """The `scm.*`, `ci.*`, `cd.*` event types, four finding types and two memory kinds (S83-04c)."""
    from tranibor_contracts import CONNECTOR_KINDS, FINDING_TYPES, INBOUND_EVENT_TYPES, MEMORY_KINDS

    for open_list in (INBOUND_EVENT_TYPES, FINDING_TYPES, MEMORY_KINDS, CONNECTOR_KINDS):
        assert open_list.declared_by(NAME) == frozenset(open_list.kept), open_list.what
    assert "scm.pr.merged" in INBOUND_EVENT_TYPES.declared_by(NAME)
    assert "flaky-test" in FINDING_TYPES.declared_by(NAME)
    assert (
        CONNECTOR_KINDS.declared_by(NAME) == {"scm", "ci", "cd"} and CONNECTOR_KINDS.plugin_of("scm") == NAME
    )
