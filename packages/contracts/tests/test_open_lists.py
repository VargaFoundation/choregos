"""The open lists of ADR 0181 (C0): every current value stays valid, an extension declares more, and
anything else is refused as before.

Five enumerations of the contracts became a pattern in their schema and an `OpenList` in Python: the
inbound event types, the finding types, the memory kinds, the connector kinds, the stage roles; `does:`
is the sixth (`STEP_EFFECTS`). Nothing is renamed: a stored event, finding or workflow reads as it did.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from enum import StrEnum

import pytest
import tranibor_contracts as contracts
from pydantic import ValidationError
from tranibor_contracts import (
    CONNECTOR_KINDS,
    FINDING_TYPES,
    INBOUND_EVENT_TYPES,
    MEMORY_KINDS,
    STAGE_ROLES,
    STEP_EFFECTS,
    ConnectorKind,
    Finding,
    FindingType,
    InboundEvent,
    InboundEventType,
    MemoryKind,
    OpenList,
    StageRole,
    Transition,
)

LISTS: dict[str, tuple[OpenList, type[StrEnum] | None]] = {
    "inbound event types": (INBOUND_EVENT_TYPES, InboundEventType),
    "finding types": (FINDING_TYPES, FindingType),
    "memory kinds": (MEMORY_KINDS, MemoryKind),
    "connector kinds": (CONNECTOR_KINDS, ConnectorKind),
    "stage roles": (STAGE_ROLES, StageRole),
    "step effects": (STEP_EFFECTS, None),
}

#: schema file -> (the JSON pointer of the open field, its list).
SCHEMA_FIELDS = {
    "inbound-event.schema.json": (("properties", "type"), INBOUND_EVENT_TYPES),
    "finding.schema.json": (("properties", "type"), FINDING_TYPES),
    "context-pack.schema.json": (("$defs", "memory", "properties", "kind"), MEMORY_KINDS),
    "workflow.schema.json": (("$defs", "transition", "properties", "does"), STEP_EFFECTS),
}


@pytest.fixture
def plugin() -> Iterator[str]:
    yield "itsm"
    for open_list, _ in LISTS.values():
        open_list.forget("itsm")
        open_list.forget("other")


@pytest.mark.parametrize("name", list(LISTS))
def test_the_core_list_is_the_enumeration_and_no_value_left(name: str) -> None:
    open_list, enum = LISTS[name]
    if enum is not None:
        assert open_list.core == {member.value for member in enum}
        for member in enum:
            assert open_list.check(member.value) is member, "a core value comes back as its member"
    else:
        assert open_list.core == {"open_pr", "merge_pr"}


@pytest.mark.parametrize("schema_name", list(SCHEMA_FIELDS))
def test_the_schema_holds_the_list_s_pattern_and_no_enum(schema_name: str) -> None:
    pointer, open_list = SCHEMA_FIELDS[schema_name]
    node = contracts.load_schema(schema_name)
    for key in pointer:
        node = node[key]
    assert "enum" not in node, f"{schema_name}: the list is open (ADR 0181)"
    assert node["pattern"] == open_list.pattern
    assert all(re.fullmatch(open_list.pattern, value) for value in open_list.core)


def test_every_current_value_still_validates_in_the_models() -> None:
    for event_type in InboundEventType:
        event = InboundEvent(type=event_type.value, source="test", delivery_id="d")
        assert event.type is event_type
        assert event.dedup_key == f"test:d:{event_type.value}"
    for finding_type in FindingType:
        found = Finding(title="a finding", type=finding_type.value, severity="low", evidence="x")
        assert found.type is finding_type
    for effect in ("open_pr", "merge_pr"):
        transition = Transition.model_validate(
            {"id": "t", "from": "a", "to": "b", "by": "ci", "does": effect}
        )
        assert transition.does == effect


def test_an_unknown_value_is_refused_naming_the_list() -> None:
    with pytest.raises(ValidationError, match=r"unknown inbound event type: 'itsm\.incident\.opened'"):
        InboundEvent(type="itsm.incident.opened", source="test", delivery_id="d")
    with pytest.raises(ValidationError, match="unknown finding type"):
        Finding(title="a finding", type="sla-breach", severity="low", evidence="x")
    with pytest.raises(ValidationError, match="unknown step effect"):
        Transition.model_validate({"id": "t", "from": "a", "to": "b", "by": "ci", "does": "close_incident"})


def test_a_declared_value_is_accepted_as_a_string_and_forgotten_with_its_plugin(plugin: str) -> None:
    INBOUND_EVENT_TYPES.declare("itsm.incident.opened", plugin=plugin)
    FINDING_TYPES.declare("sla-breach", plugin=plugin)
    STEP_EFFECTS.declare("close_incident", plugin=plugin)
    event = InboundEvent(type="itsm.incident.opened", source="servicenow", delivery_id="d")
    assert event.type == "itsm.incident.opened" and not isinstance(event.type, StrEnum)
    assert Finding(title="a finding", type="sla-breach", severity="low", evidence="x").type == "sla-breach"
    does = {"id": "t", "from": "a", "to": "b", "by": "ci", "does": "close_incident"}
    assert Transition.model_validate(does).does == "close_incident"
    assert INBOUND_EVENT_TYPES.plugin_of("itsm.incident.opened") == plugin
    assert "itsm.incident.opened" in INBOUND_EVENT_TYPES.known()

    INBOUND_EVENT_TYPES.forget(plugin)
    with pytest.raises(ValidationError):
        InboundEvent(type="itsm.incident.opened", source="servicenow", delivery_id="d")


def test_a_value_is_one_plugin_s_and_matches_the_pattern(plugin: str) -> None:
    MEMORY_KINDS.declare("runbook", plugin=plugin)
    MEMORY_KINDS.declare("runbook", plugin=plugin)  # again, for the same plugin: nothing changes
    with pytest.raises(ValueError, match="already declared by the plugin `itsm`"):
        MEMORY_KINDS.declare("runbook", plugin="other")
    with pytest.raises(ValueError, match="must match"):
        INBOUND_EVENT_TYPES.declare("Incident Opened", plugin=plugin)
    # A plugin may take over a value of the core (what C2 does with `scm.pr.opened`): still valid. The
    # suite registers `software-delivery`, which took that one over: another plugin cannot.
    INBOUND_EVENT_TYPES.declare("alert.fired", plugin=plugin)
    with pytest.raises(ValueError, match="already declared by the plugin `software-delivery`"):
        INBOUND_EVENT_TYPES.declare("scm.pr.opened", plugin=plugin)
    assert (
        InboundEvent(type="scm.pr.opened", source="github", delivery_id="d").type
        is InboundEventType.PR_OPENED
    )


def test_stage_role_has_the_roles_the_playbooks_ship() -> None:
    """`brief` and `deliver` (ADR 0110) were playbook roles the enumeration lacked (ADR 0181)."""
    assert {StageRole.BRIEF.value, StageRole.DELIVER.value} == {"brief", "deliver"}


def test_a_stored_document_reads_a_value_nobody_declares_any_more() -> None:
    """ADR 0098 §4, readers accept: a stored workflow naming a plugin's step effect keeps parsing once
    the plugin is gone (its interpreter's history replays); a new one naming it is refused."""
    from tranibor_contracts.open_lists import STORED

    does = {"id": "t", "from": "a", "to": "b", "by": "ci", "does": "close_incident"}
    assert Transition.model_validate(does, context={STORED: True}).does == "close_incident"
    with pytest.raises(ValidationError, match="unknown step effect"):
        Transition.model_validate(does)
    with pytest.raises(ValidationError, match="unknown step effect"):
        Transition.model_validate({**does, "does": "Close Incident"}, context={STORED: True})
    stored = InboundEvent.model_validate(
        {"type": "scm.pr.opened", "source": "github", "delivery_id": "d"}, context={STORED: True}
    )
    assert stored.type is InboundEventType.PR_OPENED


def test_the_values_of_software_delivery_are_kept_for_its_plugin_for_one_window() -> None:
    """Round C2 (ADR 0181): the `scm.*`, `ci.*` and `cd.*` event types, four finding types and two memory
    kinds are the `software-delivery` plugin's. The core's lists keep them for one window, each enum
    member on the window list with the release that removes it, and name the plugin for them."""
    from tranibor_contracts import FINDING_TYPES, INBOUND_EVENT_TYPES, MEMORY_KINDS
    from tranibor_contracts.window import C2, WINDOW

    expected = {
        INBOUND_EVENT_TYPES: {
            v.value for v in InboundEventType if v.value.split(".")[0] in {"scm", "ci", "cd"}
        },
        FINDING_TYPES: {"bug", "perf", "tech-debt", "flaky-test"},
        MEMORY_KINDS: {"flaky_test", "hotspot"},
    }
    windowed = {entry.where.rsplit(":", 1)[-1] for entry in WINDOW if entry.window is C2}
    for open_list, values in expected.items():
        assert set(open_list.kept) == values and set(open_list.kept.values()) == {"software-delivery"}
        assert open_list.own() == open_list.core - values
        for value in values:
            assert open_list.plugin_of(value) == "software-delivery"
            assert open_list.check(value) == value, "still read during the window"
        enum = type(open_list.check(next(iter(values))))
        assert {f"{enum.__name__}.{m.name}" for m in enum if m.value in values} <= windowed
    assert len(windowed) == 13 + 4 + 2
    assert INBOUND_EVENT_TYPES.plugin_of("tracker.item.created") is None


def test_a_kept_value_must_be_the_core_s() -> None:
    with pytest.raises(ValueError, match="not in the core's list"):
        OpenList("thing", r"^[a-z]+$", ["a"], kept={"b": "plugin"})
