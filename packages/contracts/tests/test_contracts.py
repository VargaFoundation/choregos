"""The examples must validate against both the JSON Schemas and the Python models.

This test is what prevents drift between `schemas/*.json`, `openapi.yaml` and `choregos_contracts.*`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import choregos_contracts as contracts
import pytest
from choregos_contracts import (
    ContextPack,
    ContextPolicy,
    DecisionRecord,
    Finding,
    HumanDecision,
    InboundEvent,
    Policy,
    ProjectConfig,
    StageInput,
    StageResult,
    ToolScripts,
    ToolsPolicy,
    ToolsRef,
    Workflow,
)
from choregos_contracts.stage import MAX_CONCURRENT_TOOL_CALLS
from jsonschema import Draft202012Validator, RefResolver

SCHEMAS = contracts.schemas_dir()
EXAMPLES = SCHEMAS / "examples"


def _validator(schema_name: str) -> Draft202012Validator:
    schema = contracts.load_schema(schema_name)
    store = {}
    for name in contracts.SCHEMA_FILES:
        sub = contracts.load_schema(name)
        store[sub["$id"]] = sub
        store[name] = sub
    resolver = RefResolver(base_uri=schema["$id"], referrer=schema, store=store)
    return Draft202012Validator(schema, resolver=resolver)


def _load(example: str) -> Any:
    with (EXAMPLES / example).open(encoding="utf-8") as fh:
        return json.load(fh)


def test_all_declared_schemas_exist() -> None:
    for name in contracts.SCHEMA_FILES:
        assert (SCHEMAS / name).exists(), name
    on_disk = {p.name for p in SCHEMAS.glob("*.json")}
    assert on_disk == set(contracts.SCHEMA_FILES), "SCHEMA_FILES and schemas/ have diverged"


@pytest.mark.parametrize(
    ("example", "schema_name"),
    [
        ("workflow.default-simple.json", "workflow.schema.json"),
        ("workflow.onboarding-actions.json", "workflow.schema.json"),
        ("policy.solo.json", "policy.schema.json"),
        ("project.example.json", "project.schema.json"),
        ("stage-input.example.json", "stage-input.schema.json"),
        ("stage-input.code-agent.example.json", "stage-input.schema.json"),
        ("stage-input.remote.example.json", "stage-input.schema.json"),
        ("stage-input.hosted.example.json", "stage-input.schema.json"),
        ("stage-input.prompt.example.json", "stage-input.schema.json"),
        ("stage-result.example.json", "stage-result.schema.json"),
        ("finding.example.json", "finding.schema.json"),
        ("inbound-event.example.json", "inbound-event.schema.json"),
        ("human-decision.example.json", "human-decision.schema.json"),
        ("decision-record.example.json", "decision-record.schema.json"),
        ("context-pack.example.json", "context-pack.schema.json"),
        ("event.example.json", "event.schema.json"),
        ("ui-manifest.example.json", "ui-manifest.schema.json"),
        ("plugin.example.json", "plugin.schema.json"),
        ("plugin-server.example.json", "plugin-server.schema.json"),
        ("plugin-index.example.json", "plugin-index.schema.json"),
        ("plugin-evals.example.json", "plugin-evals.schema.json"),
        ("plugin-tracker-profile.example.json", "plugin-tracker-profile.schema.json"),
        ("assistant.example.json", "assistant.schema.json"),
    ],
)
def test_example_matches_json_schema(example: str, schema_name: str) -> None:
    errors = sorted(_validator(schema_name).iter_errors(_load(example)), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)


@pytest.mark.parametrize(
    ("example", "model"),
    [
        ("workflow.default-simple.json", Workflow),
        ("workflow.onboarding-actions.json", Workflow),
        ("policy.solo.json", Policy),
        ("project.example.json", ProjectConfig),
        ("stage-input.example.json", StageInput),
        ("stage-input.code-agent.example.json", StageInput),
        ("stage-input.remote.example.json", StageInput),
        ("stage-input.hosted.example.json", StageInput),
        ("stage-input.prompt.example.json", StageInput),
        ("stage-result.example.json", StageResult),
        ("finding.example.json", Finding),
        ("inbound-event.example.json", InboundEvent),
        ("human-decision.example.json", HumanDecision),
        ("decision-record.example.json", DecisionRecord),
        ("context-pack.example.json", ContextPack),
    ],
)
def test_example_matches_python_model(example: str, model: type) -> None:
    raw = _load(example)
    obj = model.model_validate(raw)
    dumped = obj.model_dump(mode="json", by_alias=True, exclude_none=True)
    # The model must not lose any key the example fills in.
    missing = {k for k, v in raw.items() if v is not None} - set(dumped)
    assert not missing, f"keys lost by {model.__name__}: {missing}"


def _nested_model(annotation: Any) -> type | None:
    """The Pydantic model hidden behind `X | None`, `list[X]`, `dict[str, X]`, otherwise None."""
    import typing

    from pydantic import BaseModel

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        found = _nested_model(arg)
        if found is not None:
            return found
    return None


def _schema_fields(node: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The properties of an object node, following `anyOf`/`oneOf` (a nullable field)."""
    if "properties" in node:
        return dict(node["properties"])
    for combination in ("anyOf", "oneOf", "allOf"):
        for branch in node.get(combination, []):
            if isinstance(branch, dict) and "properties" in branch:
                return dict(branch["properties"])
    return {}


def _compare(trail: str, node: dict[str, Any], model: type, gaps: list[str]) -> None:
    schema_fields = _schema_fields(node)
    if not schema_fields:
        return
    model_fields = {
        (info.alias or name): info
        for name, info in model.model_fields.items()  # type: ignore[attr-defined]
    }
    missing_from_schema = set(model_fields) - set(schema_fields)
    missing_from_model = set(schema_fields) - set(model_fields)
    for name in sorted(missing_from_schema):
        gaps.append(f"{trail}.{name} : in the model {model.__name__}, absent from the schema")
    for name in sorted(missing_from_model):
        gaps.append(f"{trail}.{name} : in the schema, absent from the model {model.__name__}")
    for name in sorted(set(schema_fields) & set(model_fields)):
        sub_model = _nested_model(model_fields[name].annotation)
        if sub_model is not None:
            _compare(f"{trail}.{name}", schema_fields[name], sub_model, gaps)


@pytest.mark.parametrize(
    ("schema_name", "model"),
    [
        ("stage-result.schema.json", StageResult),
        ("stage-input.schema.json", StageInput),
        ("finding.schema.json", Finding),
        ("human-decision.schema.json", HumanDecision),
        ("policy.schema.json", Policy),
    ],
)
def test_schema_and_model_declare_the_same_fields(schema_name: str, model: type) -> None:
    """The examples are not enough to prevent drift: they only exercise the fields they carry.
    A field added to the Python model and forgotten in the schema therefore passed every check:
    it happened on 2026-09-24 with `Evidence.facts`, which the schema rejected
    (`additionalProperties: false`) while the model accepted it. Here the declarations are
    compared, not an example."""
    gaps: list[str] = []
    _compare(schema_name.split(".", maxsplit=1)[0], contracts.load_schema(schema_name), model, gaps)
    assert not gaps, "\n".join(gaps)


def test_event_schema_knows_every_emitted_type() -> None:
    """`EventType` is what the platform EMITS; the schema, what a consumer accepts.

    They had diverged without anything turning red: `workflow_failed` and `tool.called` were
    emitted, and a consumer validating against the schema rejected them.
    """
    from choregos_contracts.enums import EventType

    declares = set(contracts.load_schema("event.schema.json")["properties"]["type"]["enum"])
    assert {t.value for t in EventType} == declares


def test_openapi_is_coherent() -> None:
    spec = contracts.load_openapi()
    assert spec["openapi"].startswith("3.1")
    operations = [
        op["operationId"]
        for path in spec["paths"].values()
        for verb, op in path.items()
        if verb in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operations) == len(set(operations)), "duplicate operationId"
    # Every internal reference points to an existing component.
    text = json.dumps(spec)
    for ref in {r for r in _iter_refs(spec) if r.startswith("#/")}:
        node: Any = spec
        for part in ref.removeprefix("#/").split("/"):
            assert part in node, f"broken reference: {ref}"
            node = node[part]
    assert "choregos" in text


def test_openapi_external_refs_exist() -> None:
    spec = contracts.load_openapi()
    for ref in {r for r in _iter_refs(spec) if r.startswith("./")}:
        target = Path(contracts.contracts_dir() / ref.removeprefix("./"))
        assert target.exists(), f"referenced schema missing: {ref}"


def _iter_refs(node: Any) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                out.append(value)
            else:
                out.extend(_iter_refs(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_iter_refs(item))
    return out


def test_stage_result_helpers() -> None:
    result = StageResult.failure("agent injoignable", reason="agent_error")
    assert not result.ok
    assert result.status == "failed"
    assert ContextPack.empty("q").is_empty()


# ───────────────────────── context reduction (S37-02, ADR 0055) ─────────────────────────


def test_a_policy_without_context_reduces_nothing() -> None:
    policy = Policy.model_validate(_load("policy.solo.json"))
    assert "context" not in _load("policy.solo.json")
    for role in ("implement", "review", "triage"):
        assert not policy.context.for_role(role).active
    stage_input = StageInput.model_validate(_load("stage-input.example.json"))
    assert not stage_input.context.active


def test_a_role_override_changes_that_role_only() -> None:
    raw = _load("policy.solo.json") | {
        "context": {
            "shell_filter": "rtk",
            "compression": "headroom",
            "headroom_profile": "balanced",
            "on_unavailable": "fail",
            "rtk_exclude_commands": ["git diff"],
            "per_role": {"review": {"compression": "none"}},
        }
    }
    assert not list(_validator("policy.schema.json").iter_errors(raw))
    context = Policy.model_validate(raw).context
    implement, review = context.for_role("implement"), context.for_role("review")
    assert (implement.shell_filter, implement.compression) == ("rtk", "headroom")
    assert (review.shell_filter, review.compression) == ("rtk", "none")
    assert review.headroom_profile == "balanced" and review.on_unavailable == "fail"
    assert review.rtk_exclude_commands == ["git diff"]


def test_the_context_schema_and_model_declare_the_same_fields() -> None:
    """The `context` block, overrides included; the whole policy is compared above."""
    gaps: list[str] = []
    node = contracts.load_schema("policy.schema.json")["properties"]["context"]
    _compare("policy.context", node, ContextPolicy, gaps)
    assert not gaps, "\n".join(gaps)


def test_an_unknown_reduction_is_refused_by_the_schema_and_the_model() -> None:
    raw = _load("policy.solo.json") | {"context": {"compression": "zip"}}
    assert list(_validator("policy.schema.json").iter_errors(raw))
    with pytest.raises(ValueError, match="compression"):
        Policy.model_validate(raw)


# ───────────────────────── tool scripts (S57-05, ADR 0122) ─────────────────────────


def test_a_policy_without_tools_offers_no_scripts() -> None:
    assert "tools" not in _load("policy.solo.json")
    policy = Policy.model_validate(_load("policy.solo.json"))
    assert policy.tools.scripts is None, "it says nothing (S57-15)"
    assert policy.tools.max_concurrent_calls == 4
    for role in ("implement", "review", "triage"):
        assert policy.tools.for_role(role) is None
        assert policy.tools.said_for(role) is None
    stage_input = StageInput.model_validate(_load("stage-input.example.json"))
    assert stage_input.tools.scripts is None


def test_a_role_override_turns_scripts_off_or_bounds_them_for_that_role_only() -> None:
    raw = _load("policy.solo.json") | {
        "tools": {
            "scripts": "offer",
            "max_concurrent_calls": 4,
            "per_role": {"review": {"scripts": "off"}, "triage": {"max_concurrent_calls": 2}},
        }
    }
    assert not list(_validator("policy.schema.json").iter_errors(raw))
    tools = Policy.model_validate(raw).tools
    assert tools.for_role("review") is None
    assert tools.for_role("implement") == ToolScripts(max_concurrent_calls=4)
    assert tools.for_role("triage") == ToolScripts(max_concurrent_calls=2)
    only = Policy.model_validate(
        _load("policy.solo.json") | {"tools": {"per_role": {"plan": {"scripts": "offer"}}}}
    )
    assert only.tools.for_role("plan") == ToolScripts(max_concurrent_calls=4)
    assert only.tools.for_role("implement") is None, "the project's `off` holds for the other roles"


def test_what_the_policy_says_for_a_role_wins_over_the_agent_s_default() -> None:
    """S57-15 (ADR 0122 §9): the agent's default applies only where the policy says nothing for the role."""
    silent = ToolsPolicy()
    assert silent.for_role("implement", default="offer") == ToolScripts(max_concurrent_calls=4)
    assert silent.for_role("implement", default="off") is None
    assert silent.for_role("implement") is None, "no default: off, as before"
    said_off = ToolsPolicy.model_validate({"scripts": "off"})
    assert said_off.said_for("implement") == "off"
    assert said_off.for_role("implement", default="offer") is None, "an explicit off beats the agent's offer"
    said_offer = ToolsPolicy.model_validate({"scripts": "offer", "max_concurrent_calls": 3})
    assert said_offer.for_role("implement") == ToolScripts(max_concurrent_calls=3), "as before S57-15"
    assert said_offer.for_role("implement", default="off") == ToolScripts(max_concurrent_calls=3)
    per_role = ToolsPolicy.model_validate(
        {
            "per_role": {
                "review": {"scripts": "off"},
                "plan": {"scripts": "offer"},
                "triage": {"max_concurrent_calls": 2},
            }
        }
    )
    assert per_role.for_role("review", default="offer") is None, "the role's own off beats the agent's offer"
    assert per_role.for_role("plan", default="off") == ToolScripts(max_concurrent_calls=4)
    assert per_role.said_for("triage") is None, "a bound alone says nothing of the door"
    assert per_role.for_role("triage", default="offer") == ToolScripts(max_concurrent_calls=2)
    assert per_role.for_role("implement", default="offer") == ToolScripts(max_concurrent_calls=4)


def test_a_stored_policy_means_what_it_meant() -> None:
    """S57-15 changes the default of `tools.scripts` from `off` to "says nothing", and nothing a stored
    policy says: a document stored by 0.23.0 to 0.24.0 (`model_dump(exclude_none=True)` with the old default
    written out) still says `off`; one stored before 0.23.0 (no `tools`) or by 0.24.1 without `scripts` says
    nothing, and without an agent's default both are off, as they were. A policy that does not write
    `scripts` is stored without it, and one that writes `off` keeps it."""
    as_0_24_0 = _load("policy.solo.json") | {
        "tools": {"scripts": "off", "max_concurrent_calls": 4, "per_role": {}}
    }
    old = Policy.model_validate(as_0_24_0).tools
    assert old.said_for("implement") == "off" and old.for_role("implement", default="offer") is None
    before_tools = Policy.model_validate(_load("policy.solo.json")).tools
    assert before_tools.said_for("implement") is None and before_tools.for_role("implement") is None
    silent = Policy.model_validate(_load("policy.solo.json") | {"tools": {"max_concurrent_calls": 2}})
    stored = silent.model_dump(mode="json", exclude_none=True)["tools"]
    assert stored == {"max_concurrent_calls": 2, "per_role": {}}, "`None` is never written out"
    assert (
        Policy.model_validate(_load("policy.solo.json") | {"tools": stored}).tools.said_for("implement")
        is None
    )
    explicit = Policy.model_validate(_load("policy.solo.json") | {"tools": {"scripts": "off"}})
    assert explicit.model_dump(mode="json", exclude_none=True)["tools"]["scripts"] == "off"
    for document in (as_0_24_0, _load("policy.solo.json") | {"tools": stored}):
        assert not list(_validator("policy.schema.json").iter_errors(document))
    nulled = _load("policy.solo.json") | {"tools": {"scripts": None}}
    assert not list(_validator("policy.schema.json").iter_errors(nulled)), "a full dump writes `null`"
    assert Policy.model_validate(nulled).tools.said_for("implement") is None


def test_the_tools_schema_and_model_declare_the_same_fields() -> None:
    """The policy's `tools` block, overrides included, and the stage input's `tools.scripts`; the whole
    policy and stage input are compared above."""
    gaps: list[str] = []
    _compare(
        "policy.tools", contracts.load_schema("policy.schema.json")["properties"]["tools"], ToolsPolicy, gaps
    )
    _compare(
        "stage-input.tools",
        contracts.load_schema("stage-input.schema.json")["properties"]["tools"],
        ToolsRef,
        gaps,
    )
    assert not gaps, "\n".join(gaps)
    node = contracts.load_schema("policy.schema.json")["properties"]["tools"]["properties"]
    assert set(node) == {"scripts", "max_concurrent_calls", "per_role"}
    assert "default" not in node["scripts"] and ToolsPolicy().scripts is None, "absent: it says nothing"
    assert node["scripts"]["enum"] == ["off", "offer", None]
    bound = node["max_concurrent_calls"]
    assert (bound["minimum"], bound["maximum"], bound["default"]) == (1, MAX_CONCURRENT_TOOL_CALLS, 4)
    scripts = contracts.load_schema("stage-input.schema.json")["properties"]["tools"]["properties"]["scripts"]
    assert scripts["properties"]["max_concurrent_calls"]["maximum"] == MAX_CONCURRENT_TOOL_CALLS


@pytest.mark.parametrize(
    "tools",
    [
        {"scripts": "on"},
        {"scripts": True},
        {"max_concurrent_calls": 0},
        {"max_concurrent_calls": 17},
        {"per_role": {"review": {"scripts": "always"}}},
        {"per_role": {"review": {"max_concurrent_calls": 32}}},
        {"bridge": "on"},
    ],
)
def test_a_tools_value_outside_the_contract_is_refused_by_the_schema_and_the_model(
    tools: dict[str, Any],
) -> None:
    raw = _load("policy.solo.json") | {"tools": tools}
    assert list(_validator("policy.schema.json").iter_errors(raw))
    with pytest.raises(ValueError, match="tools"):
        Policy.model_validate(raw)


def test_a_stage_input_with_tool_scripts_validates_against_the_schema_and_the_model() -> None:
    raw = _load("stage-input.example.json")
    raw["tools"] = {**raw.get("tools", {}), "scripts": {"max_concurrent_calls": 3}}
    assert not list(_validator("stage-input.schema.json").iter_errors(raw))
    assert StageInput.model_validate(raw).tools.scripts == ToolScripts(max_concurrent_calls=3)
    raw["tools"]["scripts"] = {"max_concurrent_calls": 3, "mode": "offer"}
    assert list(_validator("stage-input.schema.json").iter_errors(raw)), "off is absent, not a mode"
    with pytest.raises(ValueError, match="mode"):
        StageInput.model_validate(raw)


def test_an_unquoted_off_in_a_yaml_policy_reads_as_off() -> None:
    """YAML 1.1 (PyYAML) reads `scripts: off` as false: what the author wrote is `off`."""
    import yaml

    raw = yaml.safe_load("tools:\n  scripts: off\n  per_role:\n    review: { scripts: off }\n")
    assert raw["tools"]["scripts"] is False, "the YAML reading this test is about"
    tools = Policy.model_validate(_load("policy.solo.json") | raw).tools
    assert tools.scripts == "off" and tools.per_role["review"].scripts == "off"
    with pytest.raises(ValueError, match="scripts"):
        Policy.model_validate(_load("policy.solo.json") | {"tools": {"scripts": True}})


def _edition_validator() -> Draft202012Validator:
    spec = contracts.load_openapi()
    schema = spec["paths"]["/edition"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    return Draft202012Validator(schema)


def test_the_edition_schema_accepts_the_community_and_the_enterprise_shapes() -> None:
    validator = _edition_validator()
    base = {"edition": "community", "features": [], "version": "0.17.16"}
    validator.validate(base)  # an older core, without the two new fields, stays valid
    validator.validate({**base, "publisher": None, "edition_version": None})
    validator.validate({**base, "edition": "enterprise", "publisher": "Acme", "edition_version": "0.2.0"})
    # the about panel's fields: all optional, so an older core stays valid
    validator.validate(
        {**base, "licence": "Apache-2.0", "edition_licence": None, "build": {"revision": None}}
    )
    validator.validate(
        {
            **base,
            "edition": "enterprise",
            "licence": "Apache-2.0",
            "edition_licence": "Acme Corp EULA",
            "build": {"revision": "0123abc"},
        }
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"publisher": 3},
        {"edition_version": 3},
        {"publisher": "x" * 81},
        {"edition_licence": 3},
        {"edition_licence": "x" * 81},
        {"licence": 3},
        {"build": {}},
        {"build": {"revision": "NOT-A-SHA"}},
        {"build": {"revision": "abc"}},
    ],
)
def test_the_edition_schema_refuses_a_malformed_publisher_or_version(extra: dict[str, Any]) -> None:
    from jsonschema import ValidationError

    with pytest.raises(ValidationError):
        _edition_validator().validate({"edition": "community", "features": [], "version": "1", **extra})


def test_stage_input_carries_the_resolved_memories() -> None:
    """S40-11: the example holds two memories, the model reads them, the schema refuses unknown fields."""
    example = _load("stage-input.example.json")
    assert [m["label"] for m in example["memories"]] == ["global:security-lessons", "project:shared"]
    parsed = contracts.StageInput.model_validate(example)
    assert [(m.label, m.mode) for m in parsed.memories] == [
        ("global:security-lessons", "read"),
        ("project:shared", "write"),
    ]
    bad = {**example, "memories": [{**example["memories"][0], "surprise": 1}]}
    assert list(_validator("stage-input.schema.json").iter_errors(bad))
    # Before S40-11 a stage input had none: the field defaults to an empty list.
    legacy = {k: v for k, v in example.items() if k != "memories"}
    assert contracts.StageInput.model_validate(legacy).memories == []


def test_a_context_pack_item_may_name_its_memory() -> None:
    item = contracts.MemoryItem(kind="decision", subject="s", content="c", memory="global:lessons")
    assert item.memory == "global:lessons"
    assert contracts.MemoryItem(kind="decision", subject="s", content="c").memory is None
    pack = _load("context-pack.example.json")
    pack["memories"][0]["memory"] = "global:lessons"
    assert not list(_validator("context-pack.schema.json").iter_errors(pack))


def test_the_network_fields_exist_in_the_models_and_the_schemas_with_the_same_defaults() -> None:
    """ADR 0070: `NetworkPolicy` grew a deny list, rates and a built-in web tools switch; `Permissions`
    carries them to the runner. A default that differs between model and schema would make the two
    disagree on what an old file means."""
    from choregos_contracts import Permissions
    from choregos_contracts.policy import NetworkPolicy

    policy_props = contracts.load_schema("policy.schema.json")["properties"]["sandbox"]["properties"][
        "network"
    ]["properties"]
    assert set(policy_props) == set(NetworkPolicy.model_fields)
    defaults = NetworkPolicy()
    assert defaults.deny_domains == [] and defaults.builtin_web_tools == "allow"
    assert defaults.per_host_per_minute is None and defaults.per_run_per_minute is None
    assert policy_props["builtin_web_tools"]["default"] == defaults.builtin_web_tools

    perms = contracts.load_schema("stage-input.schema.json")["properties"]["permissions"]["properties"]
    for name in ("deny_domains", "egress", "builtin_web_tools"):
        assert name in perms and name in Permissions.model_fields
    fresh = Permissions()
    assert fresh.egress == "unlisted" and fresh.builtin_web_tools is True and fresh.deny_domains == []
    assert perms["egress"]["default"] == "unlisted" and perms["builtin_web_tools"]["default"] is True


def test_the_policy_and_a_stage_input_with_the_new_network_keys_validate_against_the_schemas() -> None:
    policy = _load("policy.solo.json")
    policy["sandbox"]["network"].update(
        {"deny_domains": ["x.example"], "per_host_per_minute": 30, "builtin_web_tools": "deny"}
    )
    assert not list(_validator("policy.schema.json").iter_errors(policy))
    Policy.model_validate(policy)
    policy["sandbox"]["network"]["per_host_per_minute"] = 0
    assert list(_validator("policy.schema.json").iter_errors(policy))

    stage = _load("stage-input.example.json")
    stage["permissions"].update(
        {"deny_domains": ["x.example"], "egress": "listed", "builtin_web_tools": False}
    )
    assert not list(_validator("stage-input.schema.json").iter_errors(stage))
    StageInput.model_validate(stage)


def test_the_network_denied_event_is_declared_and_the_openapi_names_the_four_operations() -> None:
    from choregos_contracts.enums import EventType

    assert EventType.NETWORK_DENIED.value == "choregos.network.denied"
    assert (
        "choregos.network.denied" in contracts.load_schema("event.schema.json")["properties"]["type"]["enum"]
    )
    spec = contracts.load_openapi()
    operations = {
        op["operationId"]
        for path in spec["paths"].values()
        for verb, op in path.items()
        if verb in {"get", "post"}
    }
    assert {
        "getRunNetwork",
        "postRunNetworkDecisions",
        "getProjectNetwork",
        "getNetworkActivity",
    } <= operations
    agent = spec["components"]["schemas"]["AgentSpec"]["properties"]["network"]
    assert agent == {"$ref": "#/components/schemas/AgentNetwork"}


def test_admin_section_block_permission_is_optional_and_a_string() -> None:
    """S42-02: every block kind may carry its own permission; a non-string is refused."""
    validator = _validator("ui-manifest.schema.json")
    section = _load("ui-manifest.example.json")
    assert any("permission" in block for block in section["blocks"])
    assert not list(validator.iter_errors(section))
    for block in section["blocks"]:
        broken = {**section, "blocks": [{**block, "permission": 7}]}
        assert list(validator.iter_errors(broken)), block["kind"]
    bare = {
        **section,
        "blocks": [{k: v for k, v in b.items() if k != "permission"} for b in section["blocks"]],
    }
    assert not list(validator.iter_errors(bare))


def test_an_asset_property_names_where_its_file_goes() -> None:
    """S42-13: `format: asset` needs an `x-upload` path; accept and size limits are typed."""
    validator = _validator("ui-manifest.schema.json")
    section = _load("ui-manifest.example.json")
    form = section["blocks"][0]
    asset = form["schema"]["properties"]["badge"]
    assert asset["format"] == "asset"
    assert not list(validator.iter_errors(section))

    def with_badge(badge: dict) -> dict:
        schema = {**form["schema"], "properties": {"badge": badge}}
        return {**section, "blocks": [{**form, "schema": schema}]}

    without_upload = {k: v for k, v in asset.items() if k != "x-upload"}
    assert list(validator.iter_errors(with_badge(without_upload)))
    assert list(validator.iter_errors(with_badge({**asset, "x-upload": "https://elsewhere/up"})))
    assert list(validator.iter_errors(with_badge({**asset, "x-max-bytes": 0})))
    assert list(validator.iter_errors(with_badge({**asset, "x-accept": ["png"]})))
    assert list(validator.iter_errors(with_badge({**asset, "type": "object"})))
    # A plain string property is untouched.
    assert not list(validator.iter_errors(with_badge({"type": "string"})))


def test_the_web_search_schema_and_model_declare_the_same_fields() -> None:
    """ADR 0083: the project's narrowing of web_search, in the schema as in the model."""
    from choregos_contracts.policy import WebSearchPolicy

    gaps: list[str] = []
    node = contracts.load_schema("policy.schema.json")["properties"]["web_search"]
    _compare("policy.web_search", node, WebSearchPolicy, gaps)
    assert not gaps, "\n".join(gaps)
    raw = _load("policy.solo.json") | {"web_search": {"allow_query_reuse": "maybe"}}
    assert list(_validator("policy.schema.json").iter_errors(raw))
    with pytest.raises(ValueError, match="allow_query_reuse"):
        Policy.model_validate(raw)


def test_the_run_access_record_carries_an_optional_chain() -> None:
    """S40-22 (ADR 0077 §2): the chain is optional, and its initiator admits any `<kind>:<subject>`."""
    spec = contracts.load_openapi()
    access = spec["paths"]["/runs/{id}/access"]["get"]["responses"]["200"]["content"]["application/json"]
    schema = access["schema"]
    assert "chain" in schema["properties"] and "chain" not in schema["required"]
    validator = Draft202012Validator(schema)
    record: dict[str, Any] = {"events": 0, "refusals": 0, "access": []}
    assert not list(validator.iter_errors(record)), "a run recorded before S40-22 stays valid"
    for initiator in ("person:ada@example.org", "a2a:rovo", "schedule:nightly", None):
        chain = {
            "initiated_by": initiator,
            "runs": [
                {
                    "run_id": "T-1-plan-1",
                    "stage": "plan",
                    "agent_slug": "planner",
                    "agent_version": 3,
                    "inputs_from": [],
                },
                {
                    "run_id": "T-1-implement-1",
                    "stage": "implement",
                    "agent_slug": None,
                    "agent_version": None,
                    "inputs_from": ["T-1-plan-1"],
                },
            ],
        }
        assert not list(validator.iter_errors({**record, "chain": chain})), initiator
    broken = {**record, "chain": {"initiated_by": "person:ada@example.org", "runs": [{"run_id": "x"}]}}
    assert list(validator.iter_errors(broken)), "a run of the chain names its stage and its inputs"


def test_a_workflow_a2a_block_is_read_the_same_by_the_schema_and_the_model() -> None:
    """ADR 0078: a workflow opts in as an A2A skill; the schema and the model accept and refuse alike."""
    from choregos_contracts import WorkflowA2a

    gaps: list[str] = []
    node = contracts.load_schema("workflow.schema.json")["$defs"]["a2a"]
    _compare("workflow.a2a", node, WorkflowA2a, gaps)
    assert not gaps, "\n".join(gaps)

    validator = _validator("workflow.schema.json")
    base = _load("workflow.default-simple.json")
    opted_in = base | {"a2a": {"skill": "triage", "description": "Triage a request"}}
    assert not list(validator.iter_errors(opted_in))
    a2a = Workflow.model_validate(opted_in).a2a
    assert a2a is not None and a2a.input_modes == ["text/plain"]
    assert Workflow.model_validate(base).a2a is None
    for bad in (
        {"skill": "triage"},
        {"skill": "triage", "description": "x", "secret": "y"},
        {"skill": "Triage!", "description": "x"},
        {"skill": "triage", "description": "x", "input_modes": []},
        {"skill": "triage", "description": "x", "input_modes": ["plain"]},
    ):
        assert list(validator.iter_errors(base | {"a2a": bad})), bad
        with pytest.raises(ValueError):
            Workflow.model_validate(base | {"a2a": bad})


def test_the_platform_executors_are_read_only_in_the_contract() -> None:
    """S42-07b: executors come from the deployment's environment; only the GET is part of the contract."""
    spec = contracts.load_openapi()
    operations = {
        m for m in spec["paths"]["/platform/executors"] if m in {"get", "put", "post", "patch", "delete"}
    }
    assert operations == {"get"}


def test_a_project_a2a_block_bounds_file_parts_the_same_in_the_schema_and_the_model() -> None:
    """ADR 0078 §6 (S40-23g): a project bounds the A2A file parts it takes, 10 MiB unless it says so."""
    from choregos_contracts import A2A_MAX_FILE_BYTES_CEILING, A2A_MAX_FILE_BYTES_DEFAULT, A2aConfig

    gaps: list[str] = []
    node = contracts.load_schema("project.schema.json")["properties"]["a2a"]
    _compare("project.a2a", node, A2aConfig, gaps)
    assert not gaps, "\n".join(gaps)
    assert node["properties"]["max_file_bytes"]["default"] == A2A_MAX_FILE_BYTES_DEFAULT == 10 * 1024 * 1024
    assert node["properties"]["max_file_bytes"]["maximum"] == A2A_MAX_FILE_BYTES_CEILING

    validator = _validator("project.schema.json")
    base = _load("project.example.json")
    assert ProjectConfig.model_validate(base).a2a.max_file_bytes == A2A_MAX_FILE_BYTES_DEFAULT
    raised = base | {"a2a": {"max_file_bytes": 20 * 1024 * 1024}}
    assert not list(validator.iter_errors(raised))
    assert ProjectConfig.model_validate(raised).a2a.max_file_bytes == 20 * 1024 * 1024
    for bad in (
        {"max_file_bytes": 0},
        {"max_file_bytes": A2A_MAX_FILE_BYTES_CEILING + 1},
        {"max_file_bytes": "10MB"},
        {"max_file_bytes": 1024, "max_files": 3},
    ):
        assert list(validator.iter_errors(base | {"a2a": bad})), bad
        with pytest.raises(ValueError):
            ProjectConfig.model_validate(base | {"a2a": bad})


def test_a_knowledge_document_and_a_sync_summary_say_what_was_cut() -> None:
    """S42-09e: a spreadsheet read up to its row cap says so, per document and in the sync summary."""
    schemas = contracts.load_openapi()["components"]["schemas"]
    document = schemas["KnowledgeDocument"]["properties"]
    assert document["warnings"]["type"] == "array" and document["warnings"]["items"] == {"type": "string"}
    assert "warnings" not in schemas["KnowledgeDocument"]["required"]
    summary = schemas["KnowledgeSource"]["properties"]["last_summary"]["properties"]
    assert summary["truncated"]["type"] == "integer"
    assert summary["truncated_reasons"]["items"] == {"type": "string"}


def test_the_distillation_job_is_in_the_contract() -> None:
    """S42-10d: a distillation can be stopped and relaunched, keeps its last runs, and its spend is a
    cost kind of its own."""
    spec = contracts.load_openapi()
    paths = spec["paths"]
    stop = paths["/orgs/{org}/knowledge/{slug}/distill/stop"]["post"]
    relaunch = paths["/orgs/{org}/knowledge/{slug}/distill/relaunch"]["post"]
    assert stop["operationId"] == "stopKnowledgeDistillation"
    assert relaunch["operationId"] == "relaunchKnowledgeDistillation"
    assert relaunch["requestBody"]["required"] is False
    assert "409" in stop["responses"] and "409" in relaunch["responses"]
    run = spec["components"]["schemas"]["KnowledgeDistillRun"]
    assert {"status", "code", "requested_by", "stopped_by", "spent_usd", "parts_done"} <= set(
        run["properties"]
    )
    distillation = spec["components"]["schemas"]["KnowledgeBase"]["properties"]["distillation"]
    assert {"history", "stopped_by", "exclude", "max_usd"} <= set(distillation["properties"])
    assert distillation["properties"]["history"]["maxItems"] == 5
    for route in (
        "/projects/{id}/costs",
        "/projects/{id}/costs.csv",
        "/orgs/{org}/costs",
        "/orgs/{org}/costs.csv",
    ):
        kind = next(p for p in paths[route]["get"]["parameters"] if p.get("name") == "kind")
        assert kind["schema"]["enum"] == ["model", "tool", "distill"], route


def test_an_organisation_lists_the_slugs_it_had_and_an_alias_can_be_released() -> None:
    """ADR 0090: a rename keeps the old slug as an alias; the platform administrator can release one."""
    spec = contracts.load_openapi()
    org = spec["components"]["schemas"]["Org"]
    assert org["properties"]["aliases"]["items"] == {"type": "string"}
    assert "aliases" not in org["required"], "an older server that sends no aliases stays valid"
    release = spec["paths"]["/orgs/{org}/aliases/{alias}"]["delete"]
    assert release["operationId"] == "releaseOrgAlias"
    assert set(release["responses"]) == {"204", "403", "404"}


def test_a_trigger_maps_a_payload_by_json_pointers() -> None:
    """S56-01 (ADR 0113): the schema and the model refuse the same triggers."""
    from choregos_contracts import TriggerConfig

    gaps: list[str] = []
    node = contracts.load_schema("project.schema.json")["properties"]["triggers"]["items"]
    _compare("project.triggers", node, TriggerConfig, gaps)
    assert not gaps, "\n".join(gaps)

    validator = _validator("project.schema.json")
    base = _load("project.example.json")
    assert [t.name for t in ProjectConfig.model_validate(base).triggers] == ["incident"]
    good = {"name": "alert", "title": "{/alert/name}", "key": "/id", "fields": {"host": "/alert/host"}}
    assert not list(validator.iter_errors(base | {"triggers": [good]}))
    for bad in (
        good | {"key": "id"},
        good | {"fields": {"host": "alert.host"}},
        good | {"name": "Alert"},
        good | {"secret": "s3cret"},
        {"name": "alert"},
    ):
        assert list(validator.iter_errors(base | {"triggers": [bad]})), bad
        with pytest.raises(ValueError):
            ProjectConfig.model_validate(base | {"triggers": [bad]})
    with pytest.raises(ValueError, match="two triggers named `alert`"):
        ProjectConfig.model_validate(base | {"triggers": [good, good]})
    with pytest.raises(ValueError, match="not a JSON pointer placeholder"):
        TriggerConfig(name="alert", title="{/a~9}")


def test_a_trigger_filters_and_signs_and_a_start_rule_needs_a_condition() -> None:
    """S56-03 to S56-05 (ADR 0113): the schema and the model refuse the same configurations."""
    from choregos_contracts import StartRule

    gaps: list[str] = []
    _compare(
        "project.start_rules",
        contracts.load_schema("project.schema.json")["properties"]["start_rules"]["items"],
        StartRule,
        gaps,
    )
    assert not gaps, "\n".join(gaps)
    validator = _validator("project.schema.json")
    base = _load("project.example.json")
    trigger = {"name": "alert", "title": "t"}
    good = base | {
        "triggers": [trigger | {"when": {"/state": ["1", "2"], "/kind": "outage"}, "auth": "signature"}],
        "start_rules": [{"item_type": "incident", "states": ["New"]}],
    }
    assert not list(validator.iter_errors(good))
    ProjectConfig.model_validate(good)
    for bad in (
        base | {"triggers": [trigger | {"when": {"state": "1"}}]},
        base | {"triggers": [trigger | {"when": {"/state": []}}]},
        base | {"triggers": [trigger | {"auth": "basic"}]},
        base | {"start_rules": [{}]},
        base | {"start_rules": [{"labels_any": []}]},
        base | {"start_rules": [{"status": "New"}]},
    ):
        assert list(validator.iter_errors(bad)), bad
        with pytest.raises(ValueError):
            ProjectConfig.model_validate(bad)


def test_a_project_s_repositories_are_declared_the_same_in_the_schema_and_the_model() -> None:
    """S58-01 (ADR 0124): the repositories beside the primary, in the schema as in the model."""
    from choregos_contracts import NamedRepository, RepoConfig

    gaps: list[str] = []
    properties = contracts.load_schema("project.schema.json")["properties"]
    _compare("project.repo", properties["repo"], RepoConfig, gaps)
    _compare("project.repositories", properties["repositories"]["items"], NamedRepository, gaps)
    assert not gaps, "\n".join(gaps)

    validator = _validator("project.schema.json")
    base = _load("project.example.json")
    diamctl = {"name": "diamctl", "url": "https://github.com/acme/diamctl.git", "test_command": "make test"}
    good = base | {"repositories": [diamctl]}
    assert not list(validator.iter_errors(good))
    assert ProjectConfig.model_validate(good).repositories[0].name == "diamctl"
    assert ProjectConfig.model_validate(base).repositories == [], "a project that says nothing holds one"
    for bad in (
        {k: v for k, v in good.items() if k != "repo"},  # no primary
        base | {"repositories": [diamctl | {"name": "Diamctl"}]},
        base | {"repositories": [{"name": "diamctl"}]},
        base | {"repositories": [diamctl | {"token": "s3cret"}]},
        base | {"repo": base["repo"] | {"name": "not a slug"}},
    ):
        assert list(validator.iter_errors(bad)), bad
        with pytest.raises(ValueError):
            ProjectConfig.model_validate(bad)


def test_a_repository_name_designates_one_repository_and_a_url_has_one_name() -> None:
    """ADR 0124 §1: what the schema cannot say, the model refuses, naming the way out."""
    base = _load("project.example.json")
    diamctl = {"name": "diamctl", "url": "https://github.com/acme/diamctl.git"}
    with pytest.raises(ValueError, match="two repositories named `diamctl`"):
        ProjectConfig.model_validate(base | {"repositories": [diamctl, diamctl | {"url": "https://x/y.git"}]})
    with pytest.raises(ValueError, match=r"named `billing-api` after its URL: give `repo.name`"):
        ProjectConfig.model_validate(base | {"repositories": [diamctl | {"name": "billing-api"}]})
    with pytest.raises(ValueError, match="one repository under two names"):
        ProjectConfig.model_validate(
            base
            | {"repositories": [diamctl | {"name": "billing", "url": "https://github.com/ACME/billing-api/"}]}
        )
    with pytest.raises(ValueError, match="`repositories` without `repo`"):
        ProjectConfig.model_validate({"slug": "p", "org": "o", "repositories": [diamctl]})
    named = ProjectConfig.model_validate(
        base | {"repo": base["repo"] | {"name": "api"}, "repositories": [diamctl | {"name": "billing-api"}]}
    )
    assert named.repository_names() == ["api", "billing-api"], "a named primary frees its URL's name"


def test_the_primary_is_named_after_its_url_and_a_branch_takes_its_repository_s_prefix() -> None:
    """ADR 0124 §1 and §4: names, lookups and branch names."""
    from choregos_contracts.project import name_of_url

    assert name_of_url("https://github.com/acme/Infra.git") == "infra"
    assert name_of_url("git@github.com:acme/choregos_deploy.git") == "choregos-deploy"
    assert name_of_url("https://github.com/acme/..git") == "primary"
    config = ProjectConfig.model_validate(
        {
            "slug": "platform",
            "org": "acme",
            "repo": {"url": "https://github.com/acme/infra.git"},
            "repositories": [
                {"name": "diamctl", "url": "https://github.com/acme/diamctl.git", "branch_prefix": "fix/"}
            ],
        }
    )
    assert config.primary_name == "infra"
    assert config.repository_names() == ["infra", "diamctl"]
    assert config.repository_named(None) is config.repo
    assert config.repository_named("infra") is config.repo
    diamctl = config.repository_named("diamctl")
    assert diamctl is not None and diamctl.url.endswith("/diamctl.git")
    assert config.repository_named("fondation") is None
    assert config.branch_for("PLAT-7") == "choregos/PLAT-7", "the primary's prefix, as before"
    assert config.branch_for("PLAT-7", repo=diamctl) == "fix/PLAT-7"
    assert ProjectConfig.model_validate({"slug": "p", "org": "o"}).repository_names() == []
