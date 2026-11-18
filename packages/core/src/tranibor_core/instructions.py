# SPDX-License-Identifier: Apache-2.0
"""The instructions of a registry agent, rendered in a sandbox (ADR 0033).

A playbook is a deployment file, reviewed in a PR; an agent's instructions are written in the
console, by an organisation administrator. They are therefore rendered by an **isolated** Jinja
(`SandboxedEnvironment`): a template does not climb up to the Python classes (`{{ ''.__class__ }}`
is refused), calls no dangerous method, and reads only the variables it is given.

The same variables as a playbook: `ticket`, `spec`, `plan_markdown`, `inputs`, `allowed_paths`,
`context`, `project`. The frame the agent cannot modify (the output contract, the
invariants) is added AFTER the rendering, by the orchestrator.

An assistant's conversation (ADR 0133 point 3, S63-28) renders the same instructions with `config` (the
installation's values) and `preferences` (the caller's profile memory of the assistant), a run's variables
being empty (there is no work item), in a sandbox where a key with no value renders empty: never a sample,
never the braces.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jinja2 import ChainableUndefined, StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment


class _SampleConfig(dict[str, Any]):
    """An assistant's `{{ config.<key> }}` (ADR 0133) or `{{ preferences.<key> }}` (S63-28): any key reads
    as a sample when a version is checked at publication. The values an installation sets, and the caller's
    profile, are given at conversation time (`render_conversation_instructions`)."""

    def __getattr__(self, key: str) -> str:
        if key.startswith("_"):
            raise AttributeError(key)
        return f"<{key}>"


#: Enough to render a template to check it, when a version is published.
SAMPLE_VARIABLES: dict[str, Any] = {
    "ticket": {
        "key": "DEMO-1",
        "title": "Onboarding of Camille",
        "body": "Arrives on 2 November.",
        "fields": {},
    },
    "spec": "",
    "plan_markdown": "",
    "inputs": {},
    "allowed_paths": [],
    "context": None,
    "project": None,
    #: The production indicator sources the run can read (S22-48); empty = none configured.
    "production_indicators": [],
    #: What an assistant's installation sets (ADR 0133): any key renders.
    "config": _SampleConfig(),
    #: The caller's profile memory of an assistant (S63-28): any key renders.
    "preferences": _SampleConfig(),
}

_ENVIRONMENT = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


def render_instructions(source: str, **variables: Any) -> str:
    """The template rendered in the sandbox; raises `TemplateError` (`SecurityError` included)."""
    return _ENVIRONMENT.from_string(source).render({**SAMPLE_VARIABLES, **variables})


class _Values:
    """`config` or `preferences` at conversation time: `{{ config.items }}` is the key `items`, never the
    method of a dict; a missing key is undefined and renders empty."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = dict(data)

    def __getattr__(self, key: str) -> Any:
        try:
            return self._data[key]
        except KeyError:
            raise AttributeError(key) from None

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __bool__(self) -> bool:
        return bool(self._data)


#: A conversation hands the agent no work item: a run's variables, empty, so that a template written for both
#: (a plugin agent's work item context, `agent_catalogue.without_context` aside) renders, never as a sample.
_NO_WORK_ITEM: dict[str, Any] = {
    "ticket": {"key": "", "title": "", "body": "", "fields": {}},
    "spec": "",
    "plan_markdown": "",
    "inputs": {},
    "allowed_paths": [],
    "context": None,
    "project": None,
    "production_indicators": [],
}

_CONVERSATION = SandboxedEnvironment(
    undefined=ChainableUndefined, autoescape=False, keep_trailing_newline=True
)


def render_conversation_instructions(
    source: str, *, config: Mapping[str, Any], preferences: Mapping[str, Any]
) -> str:
    """An assistant's instructions as its conversation sends them (S63-28): `config` and `preferences`, a
    run's variables empty, no sample; a key with no value renders empty. Raises `TemplateError`
    (`SecurityError` included)."""
    return _CONVERSATION.from_string(source).render(
        {**_NO_WORK_ITEM, "config": _Values(config), "preferences": _Values(preferences)}
    )


class _TaskValues(_Values):
    """`config` or `preferences` in a task run, whose template renders under `StrictUndefined`: a missing key
    is still undefined and renders empty (`| default(...)` applies), never an error and never a sample."""

    __slots__ = ()

    def __getattr__(self, key: str) -> Any:
        if key.startswith("_"):
            raise AttributeError(key)
        return self._data.get(key, ChainableUndefined(name=key))

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key, ChainableUndefined(name=key))


def render_task_instructions(
    source: str, *, config: Mapping[str, Any], preferences: Mapping[str, Any], **variables: Any
) -> str:
    """An assistant's instructions in a task run (#1784): the run's variables as `render_instructions` takes
    them, with the installation's `config` and the subscriber's `preferences` instead of the samples; a key
    with no value renders empty. Raises `TemplateError` (`SecurityError` included)."""
    return render_instructions(
        source, **variables, config=_TaskValues(config), preferences=_TaskValues(preferences)
    )


def instruction_errors(source: str) -> list[str]:
    """What would keep the template from rendering, checked when a version is published."""
    try:
        render_instructions(source)
    except TemplateError as error:
        return [f"{type(error).__name__} : {error}"]
    return []


__all__ = [
    "SAMPLE_VARIABLES",
    "instruction_errors",
    "render_conversation_instructions",
    "render_instructions",
    "render_task_instructions",
]
