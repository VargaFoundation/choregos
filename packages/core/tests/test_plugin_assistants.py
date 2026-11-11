# SPDX-License-Identifier: Apache-2.0
"""Assistants a plugin ships (ADR 0133, S63-01): the fixture loads, and each validation rule refuses its
own case with the file and the JSON path of the field."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_core.plugins import PluginError, load_directory

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "investment-assistant"
ASSISTANT = "assistants/investment-assistant.yaml"
AGENT = "agents/investment-analyst.yaml"


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    copy = tmp_path / "investment-assistant"
    shutil.copytree(FIXTURE, copy)
    return copy


def _edit(path: Path, change: Callable[[Any], None]) -> None:
    doc = yaml.safe_load(path.read_text())
    change(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def _problems(root: Path) -> list[str]:
    with pytest.raises(PluginError) as caught:
        load_directory(root)
    return caught.value.problems


def _need(doc: dict[str, Any], name: str) -> dict[str, Any]:
    need: dict[str, Any] = next(t for t in doc["tools"] if t["need"] == name)
    return need


def test_the_fixture_loads_and_ships_what_its_assistant_names() -> None:
    loaded = load_directory(FIXTURE)
    assert list(loaded.assistants) == ["investment-assistant"]
    assert loaded.assistants["investment-assistant"]["agent"] == "investment-analyst"
    # The seed, the task workflows and the eval suite are declared files: packed and digested.
    assert {
        "knowledge/investment-policy/knowledge.yaml",
        "knowledge/investment-policy/corpus/00-purpose.md",
        "workflows/morning-brief.yaml",
        "workflows/threshold-alert.yaml",
        "evals/investment-assistant.yaml",
    } <= set(loaded.files)


def test_a_file_the_assistant_names_moves_the_digest(plugin: Path) -> None:
    before = load_directory(plugin).digest
    (plugin / "knowledge/investment-policy/corpus/00-purpose.md").write_text("# Changed\n")
    assert load_directory(plugin).digest != before


def test_an_assistant_file_is_validated_against_its_schema(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d.update(kind="Agent"))
    assert any(p.startswith(f"{ASSISTANT}:kind: ") for p in _problems(plugin))


# -- rule 1: the agent is the plugin's, and converses -----------------------------------------------------


def test_rule_1_the_agent_is_an_agent_of_the_plugin(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d.update(agent="someone-else"))
    assert (
        f"{ASSISTANT}:agent: `someone-else` is not an agent of this plugin (`contents.agents`)"
        in _problems(plugin)
    )


@pytest.mark.parametrize("runtime", [None, {"kind": "acp"}, {"kind": "voice"}])
def test_rule_1_the_agent_runs_as_prompt_or_hosted(plugin: Path, runtime: dict[str, str] | None) -> None:
    def change(doc: dict[str, Any]) -> None:
        if runtime is None:
            doc["agent"]["spec"].pop("runtime")
        else:
            doc["agent"]["spec"]["runtime"] = runtime

    _edit(plugin / AGENT, change)
    problems = _problems(plugin)
    assert any(p.startswith(f"{ASSISTANT}:agent: `investment-analyst` runs as") for p in problems), problems


# -- rule 2: the instructions read declared configuration -------------------------------------------------


def test_rule_2_a_config_key_of_the_instructions_is_declared(plugin: Path) -> None:
    _edit(
        plugin / AGENT,
        lambda d: d["agent"]["spec"].update(
            instructions="Speak for {{ config.house_name }} to {{config.desk}}."
        ),
    )
    assert _problems(plugin) == [
        f"{ASSISTANT}:config_schema: the agent's instructions read `config.desk`, which `config_schema` does "
        "not declare"
    ]


# -- rule 3: the agent's tools are declared needs and operations -------------------------------------------


def test_rule_3_a_connector_of_the_agent_is_a_declared_need(plugin: Path) -> None:
    _edit(
        plugin / AGENT,
        lambda d: d["agent"]["spec"]["mcp_servers"].append({"connector": "crm", "tools": ["x"]}),
    )
    assert _problems(plugin) == [
        f"{ASSISTANT}:tools: the agent calls `crm` (`mcp_servers/5`), which no need declares"
    ]


@pytest.mark.parametrize("tool", ["delete_article", "get_*", {"name": "purge", "arguments": {}}])
def test_rule_3_a_tool_of_the_agent_is_an_operation_of_its_need(plugin: Path, tool: Any) -> None:
    _edit(plugin / AGENT, lambda d: d["agent"]["spec"]["mcp_servers"][0]["tools"].append(tool))
    name = tool["name"] if isinstance(tool, dict) else tool
    assert _problems(plugin) == [
        f"{ASSISTANT}:tools: the agent calls `news__{name}` (`mcp_servers/0`), which is not an operation of "
        "the need `news`"
    ]


# -- rule 4: a write is approved; an inline approval is the person's own -----------------------------------


def test_rule_4_a_write_needs_an_approval(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: _need(d, "orders").pop("approval"))
    assert any(
        p.startswith(f"{ASSISTANT}:tools/4: 'approval' is a required property") for p in _problems(plugin)
    )


def test_rule_4_a_write_is_never_approved_by_none(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: _need(d, "orders").update(approval="none"))
    assert any(p.startswith(f"{ASSISTANT}:tools/4/approval: ") for p in _problems(plugin))


def test_rule_4_an_inline_approval_is_not_under_the_service_identity(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: _need(d, "orders").update(approval="inline", identity="service"))
    assert any(p.startswith(f"{ASSISTANT}:tools/4/identity: ") for p in _problems(plugin))


def test_rule_4_an_inline_approval_under_the_person_s_identity_is_accepted(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: _need(d, "orders").update(approval="inline"))
    assert load_directory(plugin).assistants


# -- rule 5: a subject memory has a subject and a resolver --------------------------------------------------


def test_rule_5_a_user_subject_memory_names_its_subject(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["memory"][2].pop("subject"))
    assert any(
        p.startswith(f"{ASSISTANT}:memory/2: 'subject' is a required property") for p in _problems(plugin)
    )


def test_rule_5_a_subject_without_resolver_needs_a_resolver_tool(plugin: Path) -> None:
    def change(doc: dict[str, Any]) -> None:
        doc["memory"][2]["subject"].pop("resolver")
        _need(doc, "market_data").pop("subject_resolver")

    _edit(plugin / ASSISTANT, change)
    assert _problems(plugin) == [
        f"{ASSISTANT}:memory/2/subject/resolver: a `user_subject` memory needs a need marked "
        "`subject_resolver: true`, or `resolver: none` for a public subject"
    ]


def test_rule_5_a_named_resolver_is_a_resolver_need(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["memory"][2]["subject"].update(resolver="news"))
    assert _problems(plugin) == [
        f"{ASSISTANT}:memory/2/subject/resolver: `news` is not a need marked `subject_resolver: true`"
    ]


def test_rule_5_a_resolver_tool_is_enough_when_none_is_named(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["memory"][2]["subject"].pop("resolver"))
    assert load_directory(plugin).assistants


# -- rule 6: personal data is retained and erasable ---------------------------------------------------------


def test_rule_6_personal_data_needs_a_conversation_retention(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["policy"]["conversation"].pop("retention_days"))
    assert any(
        p.startswith(f"{ASSISTANT}:policy/conversation: 'retention_days' is a required property")
        for p in _problems(plugin)
    )


def test_rule_6_personal_data_needs_an_erasure_mode(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["policy"]["data"].update(erasure=[]))
    assert any(p.startswith(f"{ASSISTANT}:policy/data/erasure: ") for p in _problems(plugin))


def test_rule_6_without_personal_data_no_policy_is_required(plugin: Path) -> None:
    def change(doc: dict[str, Any]) -> None:
        doc["audience"]["personal_data"] = False
        doc.pop("policy")

    _edit(plugin / ASSISTANT, change)
    assert load_directory(plugin).assistants


# -- rule 7: a task reads, and reads as the person only under a delegation ---------------------------------


def test_rule_7_a_delegated_task_reads_only(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["tasks"][0]["needs"].append("orders"))
    assert _problems(plugin) == [
        f"{ASSISTANT}:tasks/0/needs/3: a task reads only; `orders` is a write need "
        "(a write becomes a proposal)"
    ]


def test_rule_7_a_delegated_task_lists_what_it_reads(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["tasks"][0].pop("needs"))
    assert any(
        p.startswith(f"{ASSISTANT}:tasks/0: 'needs' is a required property") for p in _problems(plugin)
    )


def test_rule_7_a_scheduled_task_reads_as_the_person_only_under_a_delegation(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["tasks"][0].update(delegation="none"))
    assert _problems(plugin) == [
        f"{ASSISTANT}:tasks/0/needs/2: `brokerage` is read with the person's identity, which a task that "
        "runs without the person needs `delegation: read_only` for (ADR 0137)"
    ]


def test_rule_7_an_on_demand_task_reads_with_the_session_s_identity(plugin: Path) -> None:
    _edit(
        plugin / ASSISTANT,
        lambda d: d["tasks"][0].update(delegation="none", triggers=[{"kind": "on_demand"}]),
    )
    assert load_directory(plugin).assistants


def test_rule_7_a_poll_calls_an_operation_of_a_declared_need(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["tasks"][1]["triggers"][0].update(operation="market_data__sell"))
    assert _problems(plugin) == [
        f"{ASSISTANT}:tasks/1/triggers/0/operation: `sell` is not an operation of the need `market_data`"
    ]


# -- rule 8: English snake_case names -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("where", "change"),
    [
        ("tools/0/need", lambda d: d["tools"][0].update(need="News")),
        ("tools/0/need", lambda d: d["tools"][0].update(need="market__data")),
        ("tools/0/operations/0", lambda d: d["tools"][0]["operations"].__setitem__(0, "search-news")),
        ("memory/0/name", lambda d: d["memory"][0].update(name="profilInvestisseur")),
        ("knowledge/0/name", lambda d: d["knowledge"][0].update(name="investment-policy")),
        ("tasks/0/name", lambda d: d["tasks"][0].update(name="Morning brief")),
        (
            "surfaces/mcp/prompts/0/name",
            lambda d: d["surfaces"]["mcp"]["prompts"][0].update(name="explainMove"),
        ),
    ],
)
def test_rule_8_names_are_english_snake_case(plugin: Path, where: str, change: Callable[[Any], None]) -> None:
    _edit(plugin / ASSISTANT, change)
    assert any(p.startswith(f"{ASSISTANT}:{where}: ") for p in _problems(plugin))


def test_a_name_is_declared_once_in_its_list(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["memory"][1].update(name="investor_profile"))
    assert _problems(plugin) == [f"{ASSISTANT}:memory/1/name: `investor_profile` is declared twice"]


def test_a_need_is_declared_once(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["tools"][1].update(need="news"))
    assert f"{ASSISTANT}:tools/1/need: the need `news` is declared twice" in _problems(plugin)


# -- what the assistant names ships -----------------------------------------------------------------------


def test_a_prompt_placeholder_is_an_argument(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d["surfaces"]["mcp"]["prompts"][0].update(message="Why {ticker}?"))
    assert _problems(plugin) == [
        f"{ASSISTANT}:surfaces/mcp/prompts/0/message: `{{ticker}}` is not an argument of the prompt"
    ]


def test_a_knowledge_seed_holds_its_manifest(plugin: Path) -> None:
    (plugin / "knowledge/investment-policy/knowledge.yaml").unlink()
    assert _problems(plugin) == [
        f"{ASSISTANT}:knowledge/0/seed: `knowledge/investment-policy` is not a directory holding a "
        "knowledge.yaml"
    ]


def test_a_task_workflow_is_a_file_of_the_plugin(plugin: Path) -> None:
    (plugin / "workflows/morning-brief.yaml").unlink()
    assert f"{ASSISTANT}:tasks/0/workflow: `workflows/morning-brief.yaml` is not a file of the plugin" in (
        _problems(plugin)
    )


def test_a_named_path_cannot_leave_the_plugin(plugin: Path) -> None:
    _edit(plugin / ASSISTANT, lambda d: d.update(evals=["../outside.yaml"]))
    # Refused by the declared-files guard, before anything is read through it.
    assert "../outside.yaml: goes outside the plugin's folder" in _problems(plugin)


def test_a_link_inside_a_seed_is_refused(plugin: Path) -> None:
    (plugin / "knowledge/investment-policy/corpus/linked.md").symlink_to(plugin / "plugin.yaml")
    assert "knowledge/investment-policy/corpus/linked.md: symbolic link refused" in _problems(plugin)


def test_an_assistant_is_shipped_once(plugin: Path) -> None:
    shutil.copy(plugin / ASSISTANT, plugin / "assistants/copy.yaml")
    _edit(plugin / "plugin.yaml", lambda d: d["contents"]["assistants"].append("assistants/copy.yaml"))
    assert (
        "assistants/copy.yaml:metadata/name: assistant `investment-assistant` is shipped twice"
        in _problems(plugin)
    )


def test_a_task_workflow_must_be_a_valid_workflow(plugin: Path) -> None:
    (plugin / "workflows/morning-brief.yaml").write_text(
        "apiVersion: choregos/v1\nkind: Workflow\nmetadata: { name: morning-brief, version: 1 }\n"
        "tracker: internal\n"
    )
    problems = _problems(plugin)
    assert any(p.startswith(f"{ASSISTANT}:tasks/0/workflow: ") and "actors" in p for p in problems)


def test_the_fixture_workflows_are_valid_workflows() -> None:
    from choregos_core.dsl.parser import parse_workflow_file

    for name in ("morning-brief", "threshold-alert"):
        workflow, report = parse_workflow_file(FIXTURE / "workflows" / f"{name}.yaml")
        assert report.valid
        assert workflow.metadata.name == name
