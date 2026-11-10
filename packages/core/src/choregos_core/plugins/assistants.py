# SPDX-License-Identifier: Apache-2.0
"""Assistants a plugin ships (ADR 0133): what `assistant.schema.json` cannot say.

The schema refuses a write without approval, an inline approval under the service identity, a
`user_subject` memory without a subject, personal data without a retention and an erasure mode, and a
name that is not English snake_case (validation rules 4, 5, 6 and 8). This module checks the rest,
against the plugin the assistant ships in:

1. `agent` names an agent of the plugin whose runtime is `prompt` or `hosted` (an `acp` agent needs a
   repository and a work item);
2. every `{{ config.<key> }}` of the agent's instructions is a property of `config_schema`;
3. every connector of the agent's `mcp_servers` is a declared need, and every tool it grants an
   operation of that need (a pattern is refused: an assistant's tools are a closed list);
5. a `user_subject` memory has a resolver: `none` (a public subject), a need marked `subject_resolver`,
   or, unnamed, at least one such need;
7. a task reads only `access: read` needs, and one that runs without the person (a schedule, a poll)
   reads their identity's needs only under `delegation: read_only` (ADR 0137);

plus: names unique within their list, a prompt's `{argument}` placeholders declared, and every file the
assistant names (a knowledge seed with its `knowledge.yaml`, a task's workflow, an eval suite) present.
An eval suite is also read: the loader holds it to `conversation-evals.schema.json`, and `check_suite` to
the assistant's tools (ADR 0140). Each problem names the file and the JSON path of the field.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..conversation_evals import tools_named

#: `{{ config.company_name }}`, `{{config.x}}`, `{{ config.x | default(...) }}`.
CONFIG_REFERENCE = re.compile(r"\{\{[^}]*?\bconfig\.([A-Za-z_][A-Za-z0-9_]*)")
#: `{person}` in a prompt's message; `{{` is not a placeholder.
PROMPT_PLACEHOLDER = re.compile(r"(?<!\{)\{([a-z][a-z0-9_]*)\}(?!\})")
#: The runtimes a conversation can serve (ADR 0080).
CONVERSATIONAL_RUNTIMES = ("prompt", "hosted")
#: A subject that needs no resolver: any well-formed key is accepted (ADR 0136).
PUBLIC_SUBJECT = "none"
#: The file a knowledge seed holds at its root (the `KnowledgeBase` format templates use).
SEED_MANIFEST = "knowledge.yaml"


def named_files(doc: Any) -> tuple[list[str], list[str]]:
    """The files and the directories a (possibly invalid) assistant document names, so that the plugin
    packs and digests them: task workflows and eval suites, knowledge seeds. Anything malformed is left
    out here and reported by `check`."""
    files: list[str] = []
    directories: list[str] = []
    if not isinstance(doc, dict):
        return files, directories
    for task in _mappings(doc.get("tasks")):
        if isinstance(task.get("workflow"), str):
            files.append(task["workflow"])
    files.extend(ref for ref in doc.get("evals") or [] if isinstance(ref, str))
    for base in _mappings(doc.get("knowledge")):
        if isinstance(base.get("seed"), str):
            directories.append(base["seed"])
    return files, directories


def check(
    doc: dict[str, Any],
    ref: str,
    agents: dict[str, dict[str, Any]],
    root: Path,
    target: Any,
    problems: list[str],
) -> None:
    """Rules 1, 2, 3, 5 and 7 and the files, for a schema-valid assistant `doc` read from `ref`.
    `target(root, ref, problems)` resolves a path inside the plugin (or reports it)."""
    needs = _needs(doc, ref, problems)
    _unique(doc, ref, problems)
    agent = _agent(doc, ref, agents, problems)
    if agent is not None:
        spec = agent.get("spec") or {}
        _config_references(doc, ref, spec, problems)
        _agent_tools(ref, spec, needs, problems)
    _memory_resolvers(doc, ref, needs, problems)
    _tasks(doc, ref, needs, problems)
    _prompts(doc, ref, problems)
    _files(doc, ref, root, target, problems)


def _mappings(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)] if isinstance(value, list) else []


def _needs(doc: dict[str, Any], ref: str, problems: list[str]) -> dict[str, dict[str, Any]]:
    needs: dict[str, dict[str, Any]] = {}
    for index, tool in enumerate(_mappings(doc.get("tools"))):
        name = str(tool["need"])
        if name in needs:
            problems.append(f"{ref}:tools/{index}/need: the need `{name}` is declared twice")
        needs[name] = tool
    return needs


def _unique(doc: dict[str, Any], ref: str, problems: list[str]) -> None:
    lists = {
        "memory": _mappings(doc.get("memory")),
        "knowledge": _mappings(doc.get("knowledge")),
        "tasks": _mappings(doc.get("tasks")),
        "surfaces/mcp/prompts": _mappings(((doc.get("surfaces") or {}).get("mcp") or {}).get("prompts")),
    }
    for where, items in lists.items():
        seen: set[str] = set()
        for index, item in enumerate(items):
            name = str(item["name"])
            if name in seen:
                problems.append(f"{ref}:{where}/{index}/name: `{name}` is declared twice")
            seen.add(name)


def _agent(
    doc: dict[str, Any], ref: str, agents: dict[str, dict[str, Any]], problems: list[str]
) -> dict[str, Any] | None:
    slug = str(doc["agent"])
    entry = agents.get(slug)
    if entry is None:
        problems.append(f"{ref}:agent: `{slug}` is not an agent of this plugin (`contents.agents`)")
        return None
    agent: dict[str, Any] = entry["agent"]
    runtime = ((agent.get("spec") or {}).get("runtime") or {}).get("kind", "acp")
    if runtime not in CONVERSATIONAL_RUNTIMES:
        problems.append(
            f"{ref}:agent: `{slug}` runs as `{runtime}`; an assistant's agent runs as `prompt` or `hosted` "
            "(an `acp` agent needs a repository and a work item)"
        )
        return None
    return agent


def _config_references(doc: dict[str, Any], ref: str, spec: dict[str, Any], problems: list[str]) -> None:
    declared = set(((doc.get("config_schema") or {}).get("properties") or {}).keys())
    for key in sorted(set(CONFIG_REFERENCE.findall(str(spec.get("instructions") or "")))):
        if key not in declared:
            problems.append(
                f"{ref}:config_schema: the agent's instructions read `config.{key}`, which `config_schema` "
                "does not declare"
            )


def _agent_tools(
    ref: str, spec: dict[str, Any], needs: dict[str, dict[str, Any]], problems: list[str]
) -> None:
    for index, server in enumerate(_mappings(spec.get("mcp_servers"))):
        connector = str(server.get("connector"))
        need = needs.get(connector)
        if need is None:
            problems.append(
                f"{ref}:tools: the agent calls `{connector}` (`mcp_servers/{index}`), which no need declares"
            )
            continue
        operations = set(need["operations"])
        for tool in server.get("tools") or []:
            name = str(tool.get("name") if isinstance(tool, dict) else tool)
            if name not in operations:
                problems.append(
                    f"{ref}:tools: the agent calls `{connector}__{name}` (`mcp_servers/{index}`), "
                    f"which is not an operation of the need `{connector}`"
                )


def _memory_resolvers(
    doc: dict[str, Any], ref: str, needs: dict[str, dict[str, Any]], problems: list[str]
) -> None:
    resolvers = {name for name, tool in needs.items() if tool.get("subject_resolver")}
    for index, space in enumerate(_mappings(doc.get("memory"))):
        if space.get("scope") != "user_subject":
            continue
        resolver = (space.get("subject") or {}).get("resolver")
        where = f"{ref}:memory/{index}/subject/resolver"
        if resolver == PUBLIC_SUBJECT:
            continue
        if resolver is None and not resolvers:
            problems.append(
                f"{where}: a `user_subject` memory needs a need marked `subject_resolver: true`, or "
                "`resolver: none` for a public subject"
            )
        elif resolver is not None and resolver not in resolvers:
            problems.append(f"{where}: `{resolver}` is not a need marked `subject_resolver: true`")


def _tasks(doc: dict[str, Any], ref: str, needs: dict[str, dict[str, Any]], problems: list[str]) -> None:
    for index, task in enumerate(_mappings(doc.get("tasks"))):
        # A task triggered only on demand runs with the session's identity: it needs no delegation.
        on_demand = all(trigger.get("kind") == "on_demand" for trigger in task["triggers"])
        delegated = task["delegation"] == "read_only" or on_demand
        for position, name in enumerate(task.get("needs") or []):
            _task_need(f"{ref}:tasks/{index}/needs/{position}", str(name), delegated, needs, problems)
        for position, trigger in enumerate(task["triggers"]):
            operation = trigger.get("operation")
            if not isinstance(operation, str):
                continue
            where = f"{ref}:tasks/{index}/triggers/{position}/operation"
            name, _, op = operation.partition("__")
            if _task_need(where, name, delegated, needs, problems) and op not in needs[name]["operations"]:
                problems.append(f"{where}: `{op}` is not an operation of the need `{name}`")


def _task_need(
    where: str, name: str, delegated: bool, needs: dict[str, dict[str, Any]], problems: list[str]
) -> bool:
    need = needs.get(name)
    if need is None:
        problems.append(f"{where}: `{name}` is not a declared need")
        return False
    if need["access"] != "read":
        problems.append(f"{where}: a task reads only; `{name}` is a write need (a write becomes a proposal)")
    elif not delegated and need["identity"] != "service":
        problems.append(
            f"{where}: `{name}` is read with the person's identity, which a task that runs without the "
            "person needs `delegation: read_only` for (ADR 0137)"
        )
    return True


def _prompts(doc: dict[str, Any], ref: str, problems: list[str]) -> None:
    prompts = _mappings(((doc.get("surfaces") or {}).get("mcp") or {}).get("prompts"))
    for index, prompt in enumerate(prompts):
        declared = {str(a["name"]) for a in _mappings(prompt.get("arguments"))}
        for name in sorted(set(PROMPT_PLACEHOLDER.findall(str(prompt["message"]))) - declared):
            problems.append(
                f"{ref}:surfaces/mcp/prompts/{index}/message: `{{{name}}}` is not an argument of the prompt"
            )


def _files(doc: dict[str, Any], ref: str, root: Path, target: Any, problems: list[str]) -> None:
    for index, task in enumerate(_mappings(doc.get("tasks"))):
        path = target(root, task["workflow"], problems)
        if path is not None and not path.is_file():
            problems.append(f"{ref}:tasks/{index}/workflow: `{task['workflow']}` is not a file of the plugin")
    for index, suite in enumerate(doc.get("evals") or []):
        path = target(root, suite, problems)
        if path is not None and not path.is_file():
            problems.append(f"{ref}:evals/{index}: `{suite}` is not a file of the plugin")
    for index, base in enumerate(_mappings(doc.get("knowledge"))):
        path = target(root, base["seed"], problems)
        if path is not None and not (path / SEED_MANIFEST).is_file():
            problems.append(
                f"{ref}:knowledge/{index}/seed: `{base['seed']}` is not a directory holding a {SEED_MANIFEST}"
            )


def check_suite(suite: dict[str, Any], ref: str, doc: dict[str, Any], problems: list[str]) -> None:
    """A schema-valid conversation suite `suite`, read from `ref`, against the assistant `doc` that names
    it (ADR 0140): it evaluates the assistant's agent, its case names are unique, and every tool an
    assertion or a scripted result names is a `need__operation` of the assistant."""
    if suite["agent"] != doc["agent"]:
        problems.append(
            f"{ref}:agent: the suite evaluates `{suite['agent']}`, the agent of assistant "
            f"`{doc['metadata']['name']}` is `{doc['agent']}`"
        )
    known = {f"{tool['need']}__{op}" for tool in _mappings(doc.get("tools")) for op in tool["operations"]}
    seen: set[str] = set()
    for index, case in enumerate(suite["cases"]):
        if case["name"] in seen:
            problems.append(f"{ref}:cases/{index}/name: case `{case['name']}` is declared twice")
        seen.add(case["name"])
        for where, tool in tools_named(case):
            if tool not in known:
                problems.append(
                    f"{ref}:cases/{index}/{where}: `{tool}` is not a `need__operation` of the "
                    "assistant's tools"
                )
