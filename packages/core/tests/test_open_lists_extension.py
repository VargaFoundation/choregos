"""An INSTALLED extension extends the open lists of ADR 0181 (C0), and the platform's readers accept what
it declares: an inbound event type, a finding type, a memory kind, a connector kind and a stage role.

The extension is a real module with its `.dist-info`, loaded by `load_extensions()` as at start-up: a
test plugin, `itsm`, declares one value of each list. Before it loads each value is refused, as every
value outside the core's list always was; after, the contract, the core and the adapters take it.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError

EXTENSION = """
from pathlib import Path

from tranibor_adapters import register
from tranibor_contracts import FINDING_TYPES, INBOUND_EVENT_TYPES, MEMORY_KINDS
from tranibor_core.stage_roles import declare_stage_roles


class Desk:
    def __init__(self, config):
        self.config = config


def plug_in():
    INBOUND_EVENT_TYPES.declare("itsm.incident.opened", plugin="itsm")
    FINDING_TYPES.declare("sla-breach", plugin="itsm")
    MEMORY_KINDS.declare("runbook", plugin="itsm")
    register("service_desk", "servicenow", plugin="itsm")(Desk)
    declare_stage_roles(Path(__file__).parent / "itsm_roles", plugin="itsm")
"""

PLAYBOOK = "# Incident responder\\n\\nTriage the incident {{ work_item.key | default('') }}.\\n"

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: incidents, version: 1 }
initial: inbox
actors:
  responder: { type: agent, role: incident_response, model: "profile:standard" }
states:
  inbox: { display: To do }
  done: { display: Done, terminal: true }
transitions:
  - { id: t-respond, from: inbox, to: done, by: responder, outputs: [summary] }
"""


def _event() -> Any:
    from tranibor_contracts import InboundEvent

    return InboundEvent(type="itsm.incident.opened", source="servicenow", delivery_id="d")


def _finding() -> Any:
    from tranibor_contracts import Finding

    return Finding(title="SLA missed", type="sla-breach", severity="high", evidence="INC-1")


def _fact() -> Any:
    from tranibor_core.domain import Fact

    return Fact(kind="runbook", subject="disk full", content="clear /var/log")


def _role_warnings() -> list[str]:
    from tranibor_core import parse_workflow

    _, report = parse_workflow(WORKFLOW, strict=False)
    return [w.code for w in report.warnings if w.code == "role.playbook_not_found"]


@pytest.fixture
def extension(tmp_path: pathlib.Path) -> Iterator[None]:
    from tranibor_adapters import load_extensions
    from tranibor_adapters.registry import _REGISTRY, _SPECS
    from tranibor_contracts import CONNECTOR_KINDS, FINDING_TYPES, INBOUND_EVENT_TYPES, MEMORY_KINDS
    from tranibor_core.stage_roles import forget_stage_roles

    root = tmp_path / "site"
    (root / "itsm_roles").mkdir(parents=True)
    (root / "itsm_roles" / "incident_response.md").write_text(PLAYBOOK, encoding="utf-8")
    (root / "extension_itsm_lists.py").write_text(EXTENSION, encoding="utf-8")
    info = root / "extension_itsm_lists-0.1.0.dist-info"
    info.mkdir()
    metadata = "Metadata-Version: 2.1\nName: extension_itsm_lists\nVersion: 0.1.0\n"
    (info / "METADATA").write_text(metadata, encoding="utf-8")
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\nextension_itsm_lists = extension_itsm_lists:plug_in\n", encoding="utf-8"
    )
    sys.path.insert(0, str(root))
    try:
        assert "extension_itsm_lists" in load_extensions()
        yield
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("extension_itsm_lists", None)
        for open_list in (INBOUND_EVENT_TYPES, FINDING_TYPES, MEMORY_KINDS, CONNECTOR_KINDS):
            open_list.forget("itsm")
        forget_stage_roles("itsm")
        _REGISTRY.pop(("service_desk", "servicenow"), None)
        _SPECS.pop(("service_desk", "servicenow"), None)


def test_without_the_extension_each_value_is_refused() -> None:
    from tranibor_adapters import available
    from tranibor_contracts import CONNECTOR_KINDS

    for build in (_event, _finding, _fact):
        with pytest.raises(ValidationError):
            build()
    assert available("service_desk") == [] and "service_desk" not in CONNECTOR_KINDS.known()
    assert _role_warnings() == ["role.playbook_not_found"]


def test_with_the_extension_the_platform_takes_what_it_declares(extension: None) -> None:
    from tranibor_adapters import available, spec_of
    from tranibor_contracts import CONNECTOR_KINDS, STAGE_ROLES
    from tranibor_playbooks import playbook_path, render_playbook

    assert _event().type == "itsm.incident.opened"
    assert _finding().type == "sla-breach"
    assert _fact().kind == "runbook"
    assert available("service_desk") == ["servicenow"]
    spec = spec_of("service_desk", "servicenow")
    assert spec is not None and spec.capabilities == ("service_desk",)
    assert CONNECTOR_KINDS.plugin_of("service_desk") == "itsm"
    assert STAGE_ROLES.plugin_of("incident_response") == "itsm"
    assert playbook_path("incident_response").name == "incident_response.md"
    assert "Triage the incident" in render_playbook("incident_response", work_item={"key": "INC-1"})
    assert _role_warnings() == [], "the plugin's role has its playbook"


def test_a_memory_read_back_keeps_a_kind_nobody_declares_any_more() -> None:
    """ADR 0098 §4, readers accept: the store holds what an uninstalled plugin's extension wrote."""
    from tranibor_contracts import MemoryKind
    from tranibor_core.domain import Memory

    assert Memory(id="m", kind="runbook", subject="s", content="c").kind == "runbook"
    assert Memory(id="m", kind="hotspot", subject="s", content="c").kind is MemoryKind.HOTSPOT
    with pytest.raises(ValidationError):
        Memory(id="m", kind="Run Book", subject="s", content="c")
