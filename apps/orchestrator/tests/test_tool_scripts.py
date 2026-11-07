"""The run's tool scripts: the organisation's switch (its setting, else the deployment's default, never past
the deployment's brake; S57-05b), the organisation's ceiling, the policy and the role (S57-05, ADR 0122 §6).
Off is never sent: a runner older than ADR 0122 refuses `tools.scripts`."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_contracts import StageInput, ToolScripts, ToolsPolicy
from choregos_core.tool_scripts_switch import org_tool_scripts, tool_scripts_refusal, tool_scripts_switched_on
from choregos_orchestrator.activities import evals
from choregos_orchestrator.activities import stage as stage_activities
from choregos_orchestrator.config import reset_settings_cache
from choregos_orchestrator.context_reduction import resolve_tool_scripts, stage_input_payload

from .conftest import Fixture

OFFERED = {"scripts": "offer", "per_role": {"review": {"scripts": "off"}}}
#: No ceiling and an agent that declares no default: the resolver as it was before S57-15.
NO_DEFAULT: dict[str, Any] = {"ceiling": None, "agent_default": None}


@pytest.fixture
def switch_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The deployment lets the policy decide (`CHOREGOS_TOOL_SCRIPTS=on`); off is its default."""
    monkeypatch.setenv("CHOREGOS_TOOL_SCRIPTS", "on")
    reset_settings_cache()
    yield
    monkeypatch.delenv("CHOREGOS_TOOL_SCRIPTS")
    reset_settings_cache()


async def _set_tools(project_id: str, tools: dict[str, Any] | None) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, project_id)
        assert row is not None
        model = policy_model(row).model_dump(mode="json", exclude_none=True)
        model.pop("tools", None)
        if tools is not None:
            model["tools"] = tools
        row.json_doc = model


async def _set_ceiling(project_id: str, ceiling: dict[str, Any]) -> None:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.definitions import CEILING_KEY

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        organisation = await session.get(Organization, project.org_id)
        assert organisation is not None
        organisation.settings = {**(organisation.settings or {}), CEILING_KEY: {"ceiling": ceiling}}


def _plan(setup: Fixture, role: str) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": f"t-{role}",
        "role": role,
        "from_state": "in_progress",
        "to_state": "verifying",
        "actor": "dev",
        "attempt": 1,
        "backend": "claude-code",
        "model_request": "profile:by_size",
    }


async def _tools_of(setup: Fixture, role: str) -> dict[str, Any]:
    prepared = await stage_activities.prepare_stage(_plan(setup, role))
    tools = prepared["stage_input"]["tools"]
    assert isinstance(tools, dict)
    return tools


# ───────────────────────── the resolver ─────────────────────────


def test_the_kill_switch_gives_none_whatever_the_policy() -> None:
    offered = ToolsPolicy.model_validate({"scripts": "offer"})
    assert resolve_tool_scripts(offered, "implement", switched_on=False, **NO_DEFAULT) is None
    assert resolve_tool_scripts(offered, "implement", switched_on=True, **NO_DEFAULT) == ToolScripts(
        max_concurrent_calls=4
    )


def test_a_role_turned_off_gets_none_and_the_others_the_project_s_bound() -> None:
    tools = ToolsPolicy.model_validate(
        {**OFFERED, "per_role": {**OFFERED["per_role"], "triage": {"max_concurrent_calls": 2}}}
    )
    assert resolve_tool_scripts(tools, "review", switched_on=True, **NO_DEFAULT) is None
    assert resolve_tool_scripts(tools, "implement", switched_on=True, **NO_DEFAULT) == ToolScripts(
        max_concurrent_calls=4
    )
    assert resolve_tool_scripts(tools, "triage", switched_on=True, **NO_DEFAULT) == ToolScripts(
        max_concurrent_calls=2
    )
    assert resolve_tool_scripts(ToolsPolicy(), "implement", switched_on=True, **NO_DEFAULT) is None


def test_an_inactive_setting_is_absent_from_the_payload_an_active_one_present(
    stage_input_example: dict[str, Any],
) -> None:
    stage_input = StageInput.model_validate(stage_input_example)
    assert stage_input.tools.scripts is None
    payload = stage_input_payload(stage_input)
    assert "scripts" not in payload["tools"], "an older runner refuses the key"
    assert "mcp" in payload["tools"], "the rest of `tools` is sent as before"
    stage_input.tools.scripts = ToolScripts(max_concurrent_calls=3)
    assert stage_input_payload(stage_input)["tools"]["scripts"] == {"max_concurrent_calls": 3}


@pytest.fixture
def stage_input_example() -> dict[str, Any]:
    import json

    import choregos_contracts as contracts

    with (contracts.schemas_dir() / "examples" / "stage-input.example.json").open(encoding="utf-8") as fh:
        loaded: dict[str, Any] = json.load(fh)
    return loaded


# ───────────────────────── prepare_stage ─────────────────────────


async def test_a_policy_without_tools_sends_none(setup: Fixture, switch_on: None) -> None:
    await _set_tools(setup.project_id, None)
    assert "scripts" not in await _tools_of(setup, "implement")


async def test_the_policy_and_its_role_override_reach_the_stage_input(
    setup: Fixture, switch_on: None
) -> None:
    await _set_tools(setup.project_id, OFFERED)
    assert (await _tools_of(setup, "implement"))["scripts"] == {"max_concurrent_calls": 4}
    assert "scripts" not in await _tools_of(setup, "review"), "`per_role: {review: {scripts: off}}`"


async def test_the_kill_switch_is_off_by_default_and_wins_over_the_policy(setup: Fixture) -> None:
    await _set_tools(setup.project_id, OFFERED)
    assert "scripts" not in await _tools_of(setup, "implement")


async def test_the_organisation_s_ceiling_forbids_them(setup: Fixture, switch_on: None) -> None:
    """A policy saved before the ceiling: the policy that runs is clamped to it (S27-05)."""
    await _set_tools(setup.project_id, {"scripts": "offer", "per_role": {"triage": {"scripts": "offer"}}})
    await _set_ceiling(setup.project_id, {"tools": {"scripts": "off"}})
    assert "scripts" not in await _tools_of(setup, "implement")
    assert "scripts" not in await _tools_of(setup, "triage"), "a role's own `offer` is clamped too"


async def test_an_eval_cell_runs_the_mcp_door_only(setup: Fixture, switch_on: None) -> None:
    """An `mcp` cell (the default, S57-09) gives its run the MCP door only, whatever the policy offers."""
    await _set_tools(setup.project_id, OFFERED)
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, setup.project_id)
        assert row is not None
        model = policy_model(row).model_dump(mode="json", exclude_none=True)
        model["memory"] = {**model.get("memory", {}), "enabled": False}
        row.json_doc = model
    seeded = await evals.seed_eval_ticket(
        {
            "project_slug": "billing-api",
            "matrix_id": "evals-nightly",
            "backend": "claude-code",
            "model": "platform/standard",
            "with_memory": False,
            "context": "none",
            "fixture": "py-rounding-bug",
            "role": "implement",
        }
    )
    assert not seeded["skipped"], seeded["reason"]
    prepared = await stage_activities.prepare_stage(seeded["plan"])
    assert "scripts" not in prepared["stage_input"]["tools"]


# ───────────────────────── the organisation's switch (S57-05b) ─────────────────────────

_ATTEMPTS = itertools.count(1)


async def _fresh_tools_of(setup: Fixture, role: str) -> dict[str, Any]:
    """`_tools_of` for a new attempt each time: `prepare_stage` hands back the stage input of a run it already
    prepared (ADR 0008), so a switch changed between two calls needs a new run."""
    prepared = await stage_activities.prepare_stage({**_plan(setup, role), "attempt": next(_ATTEMPTS)})
    assert not prepared.get("reused"), "a new run, prepared now"
    tools = prepared["stage_input"]["tools"]
    assert isinstance(tools, dict)
    return tools


@pytest.fixture
def deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Sets the deployment's default (`CHOREGOS_TOOL_SCRIPTS`) and brake (`CHOREGOS_TOOL_SCRIPTS_ALLOWED`)."""

    def _set(*, default: bool | None = None, allowed: bool | None = None) -> None:
        for name, value in (("CHOREGOS_TOOL_SCRIPTS", default), ("CHOREGOS_TOOL_SCRIPTS_ALLOWED", allowed)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, "on" if value else "off")
        reset_settings_cache()

    yield _set
    monkeypatch.delenv("CHOREGOS_TOOL_SCRIPTS", raising=False)
    monkeypatch.delenv("CHOREGOS_TOOL_SCRIPTS_ALLOWED", raising=False)
    reset_settings_cache()


async def _set_org_choice(project_id: str, choice: str | None) -> None:
    """What `PATCH /orgs/{org}` with `tool_scripts` writes (`apps/api/tests/test_org_tool_scripts.py`)."""
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        organisation = await session.get(Organization, project.org_id)
        assert organisation is not None
        settings = {k: v for k, v in (organisation.settings or {}).items() if k != "tool_scripts"}
        if choice is not None:
            settings["tool_scripts"] = choice
        organisation.settings = settings


@pytest.mark.parametrize(
    ("choice", "default", "allowed", "on"),
    [
        ("enabled", False, True, True),
        ("enabled", True, True, True),
        ("disabled", True, True, False),
        ("disabled", False, True, False),
        (None, True, True, True),
        (None, False, True, False),
        ("enabled", True, False, False),
        ("disabled", True, False, False),
        (None, True, False, False),
    ],
)
def test_the_organisation_decides_the_default_follows_and_the_brake_wins(
    choice: Any, default: bool, allowed: bool, on: bool
) -> None:
    assert tool_scripts_switched_on(choice, deployment_default=default, deployment_allowed=allowed) is on
    refusal = tool_scripts_refusal(choice, deployment_default=default, deployment_allowed=allowed)
    assert (refusal is None) is on
    if not allowed:
        assert refusal is not None and "CHOREGOS_TOOL_SCRIPTS_ALLOWED" in refusal
    elif choice == "disabled":
        assert refusal is not None and "organisation has tool scripts disabled" in refusal
    elif not on:
        assert refusal is not None and "CHOREGOS_TOOL_SCRIPTS)" in refusal


def test_a_stored_value_outside_the_two_choices_turns_nothing_on() -> None:
    assert org_tool_scripts({"tool_scripts": "enabled"}) == "enabled"
    assert org_tool_scripts({"tool_scripts": "disabled"}) == "disabled"
    for stored in ({}, None, {"tool_scripts": True}, {"tool_scripts": "on"}, {"tool_scripts": None}):
        assert org_tool_scripts(stored) is None, stored


async def test_an_organisation_that_enables_them_gets_them_though_the_deployment_s_default_is_off(
    setup: Fixture, deployment: Any
) -> None:
    deployment()  # the chart's defaults: `enabled: false`, `allowed: true`
    await _set_tools(setup.project_id, OFFERED)
    assert "scripts" not in await _fresh_tools_of(setup, "implement"), "no choice: the default, off"
    await _set_org_choice(setup.project_id, "enabled")
    assert (await _fresh_tools_of(setup, "implement"))["scripts"] == {"max_concurrent_calls": 4}
    assert "scripts" not in await _fresh_tools_of(setup, "review"), "the policy's role still decides past it"


async def test_an_organisation_that_disables_them_gets_none_though_the_deployment_s_default_is_on(
    setup: Fixture, deployment: Any
) -> None:
    deployment(default=True)
    await _set_tools(setup.project_id, OFFERED)
    assert (await _fresh_tools_of(setup, "implement"))["scripts"] == {"max_concurrent_calls": 4}
    await _set_org_choice(setup.project_id, "disabled")
    assert "scripts" not in await _fresh_tools_of(setup, "implement")


async def test_an_organisation_that_does_not_choose_follows_the_deployment_s_default(
    setup: Fixture, deployment: Any
) -> None:
    await _set_tools(setup.project_id, OFFERED)
    await _set_org_choice(setup.project_id, None)
    deployment(default=True)
    assert (await _fresh_tools_of(setup, "implement"))["scripts"] == {"max_concurrent_calls": 4}
    deployment(default=False)
    assert "scripts" not in await _fresh_tools_of(setup, "implement")


async def test_the_deployment_s_brake_wins_over_an_organisation_that_enables_them(
    setup: Fixture, deployment: Any
) -> None:
    await _set_tools(setup.project_id, OFFERED)
    await _set_org_choice(setup.project_id, "enabled")
    deployment(default=True, allowed=False)
    assert "scripts" not in await _fresh_tools_of(setup, "implement")
    deployment(default=True, allowed=True)
    assert (await _fresh_tools_of(setup, "implement"))["scripts"] == {"max_concurrent_calls": 4}


async def test_the_ceiling_still_forbids_them_in_an_organisation_that_enables_them(
    setup: Fixture, deployment: Any
) -> None:
    deployment()
    await _set_tools(setup.project_id, OFFERED)
    await _set_org_choice(setup.project_id, "enabled")
    await _set_ceiling(setup.project_id, {"tools": {"scripts": "off"}})
    assert "scripts" not in await _fresh_tools_of(setup, "implement")


async def _eval_project(setup: Fixture) -> None:
    """The matrix's eval project: memory off, no `tools` in its policy (the cell decides)."""
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, setup.project_id)
        assert row is not None
        model = policy_model(row).model_dump(mode="json", exclude_none=True)
        model["memory"] = {**model.get("memory", {}), "enabled": False}
        model.pop("tools", None)
        row.json_doc = model


def _scripts_cell() -> dict[str, Any]:
    return {
        "project_slug": "billing-api",
        "matrix_id": "evals-nightly",
        "backend": "claude-code",
        "model": "platform/standard",
        "with_memory": False,
        "context": "none",
        "tools": "scripts",
        "fixture": "py-rounding-bug",
        "role": "implement",
    }


async def test_the_eval_organisation_enables_them_through_its_setting(
    setup: Fixture, deployment: Any
) -> None:
    """The runbook's eval organisation (`docs/runbooks/live-eval-matrix.md`): no deployment flag, its own
    `tool_scripts: enabled`; a `scripts` cell is seeded and its run gets the bridge."""
    deployment()
    await _eval_project(setup)
    refused = await evals.seed_eval_ticket(_scripts_cell())
    assert refused["skipped"] and "CHOREGOS_TOOL_SCRIPTS)" in refused["reason"]
    await _set_org_choice(setup.project_id, "enabled")
    seeded = await evals.seed_eval_ticket(_scripts_cell())
    assert not seeded["skipped"], seeded["reason"]
    prepared = await stage_activities.prepare_stage(seeded["plan"])
    assert prepared["stage_input"]["tools"]["scripts"] == {"max_concurrent_calls": 4}
    await _set_org_choice(setup.project_id, "disabled")
    replanned = await stage_activities.prepare_stage(
        {**seeded["plan"], "attempt": seeded["plan"]["attempt"] + 1}
    )
    assert "scripts" not in replanned["stage_input"]["tools"], "a cell never runs against its organisation"


async def test_a_scripts_cell_is_skipped_naming_the_switch_that_refuses_it(
    setup: Fixture, deployment: Any
) -> None:
    await _eval_project(setup)
    deployment(default=True)
    await _set_org_choice(setup.project_id, "disabled")
    disabled = await evals.seed_eval_ticket(_scripts_cell())
    assert disabled["skipped"] and "organisation has tool scripts disabled" in disabled["reason"]
    await _set_org_choice(setup.project_id, "enabled")
    deployment(default=True, allowed=False)
    braked = await evals.seed_eval_ticket(_scripts_cell())
    assert braked["skipped"] and "CHOREGOS_TOOL_SCRIPTS_ALLOWED" in braked["reason"]


# ───────────────────────── the agent's measured default (S57-15, ADR 0122 §9) ─────────────────────────

AGENT = "net-analyst"


async def _agent_with(setup: Fixture, tools: dict[str, Any] | None) -> None:
    """A registry agent of the project's organisation; `tools` is its spec's `tools` (`None`: no default)."""
    from choregos_api.db.models import Agent, AgentVersion, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        agent = Agent(
            org_id=project.org_id,
            slug=AGENT,
            kind="internal",
            display_name=AGENT,
            status="active",
            scope="org",
        )
        session.add(agent)
        await session.flush()
        spec: dict[str, Any] = {"instructions": "Diagnose the network.", "model": "profile:standard"}
        if tools is not None:
            spec["tools"] = tools
        session.add(
            AgentVersion(agent_id=agent.id, org_id=project.org_id, version=1, spec=spec, checksum="sha256:v1")
        )


async def _agent_tools_of(setup: Fixture, role: str = "implement") -> dict[str, Any]:
    """The stage input's `tools` of a new run of `AGENT` in `role`."""
    plan = {**_plan(setup, role), "agent": AGENT, "attempt": next(_ATTEMPTS)}
    prepared = await stage_activities.prepare_stage(plan)
    assert not prepared.get("reused"), "a new run, prepared now"
    tools = prepared["stage_input"]["tools"]
    assert isinstance(tools, dict)
    return tools


OFFERS = {"scripts": "offer"}
FOUR = {"max_concurrent_calls": 4}


@pytest.mark.parametrize(
    ("policy_tools", "agent_tools", "role", "expected"),
    [
        (None, OFFERS, "implement", FOUR),
        ({"max_concurrent_calls": 2}, OFFERS, "implement", {"max_concurrent_calls": 2}),
        ({"scripts": "off"}, OFFERS, "implement", None),
        ({"scripts": "offer"}, None, "implement", FOUR),
        (
            {"scripts": "offer", "max_concurrent_calls": 3},
            {"scripts": "off"},
            "implement",
            {"max_concurrent_calls": 3},
        ),
        (None, None, "implement", None),
        (None, {"scripts": "off"}, "implement", None),
        ({"per_role": {"review": {"scripts": "off"}}}, OFFERS, "review", None),
        ({"per_role": {"review": {"scripts": "off"}}}, OFFERS, "implement", FOUR),
        ({"scripts": "off", "per_role": {"plan": {"scripts": "offer"}}}, None, "plan", FOUR),
        (
            {"per_role": {"triage": {"max_concurrent_calls": 2}}},
            OFFERS,
            "triage",
            {"max_concurrent_calls": 2},
        ),
        ({"scripts": "off", "per_role": {"triage": {"max_concurrent_calls": 2}}}, OFFERS, "triage", None),
    ],
    ids=[
        "policy-silent-agent-offers",
        "policy-silent-bounds-the-agent-s-offer",
        "policy-off-beats-agent-offer",
        "policy-offers-agent-silent-as-before",
        "policy-offers-agent-off",
        "both-silent-off",
        "agent-off-is-no-default",
        "role-off-beats-agent-offer",
        "other-role-keeps-agent-offer",
        "role-offers-over-project-off",
        "role-bound-alone-says-nothing",
        "role-bound-under-project-off",
    ],
)
async def test_the_policy_decides_where_it_says_something_the_agent_s_default_elsewhere(
    setup: Fixture,
    deployment: Any,
    policy_tools: dict[str, Any] | None,
    agent_tools: dict[str, Any] | None,
    role: str,
    expected: dict[str, Any] | None,
) -> None:
    deployment(default=True)
    await _set_tools(setup.project_id, policy_tools)
    await _agent_with(setup, agent_tools)
    tools = await _agent_tools_of(setup, role)
    assert tools.get("scripts") == expected, tools


@pytest.mark.parametrize(
    ("default", "allowed", "choice", "offered"),
    [
        (False, True, None, False),
        (True, True, "disabled", False),
        (False, True, "enabled", True),
        (True, False, "enabled", False),
    ],
    ids=["deployment-default-off", "organisation-disabled", "organisation-enabled", "deployment-brake"],
)
async def test_the_agent_s_default_never_passes_the_organisation_s_switch(
    setup: Fixture, deployment: Any, default: bool, allowed: bool, choice: str | None, offered: bool
) -> None:
    await _set_tools(setup.project_id, None)
    await _agent_with(setup, OFFERS)
    deployment(default=default, allowed=allowed)
    await _set_org_choice(setup.project_id, choice)
    assert (await _agent_tools_of(setup)).get("scripts") == (FOUR if offered else None)


async def test_the_agent_s_default_never_passes_the_ceiling(setup: Fixture, deployment: Any) -> None:
    """A policy that says nothing is not clamped (`clamp` lowers only an `offer`): the ceiling is asked of
    the run itself."""
    deployment(default=True)
    await _set_tools(setup.project_id, None)
    await _agent_with(setup, OFFERS)
    assert (await _agent_tools_of(setup))["scripts"] == FOUR
    await _set_ceiling(setup.project_id, {"tools": {"scripts": "off"}})
    assert "scripts" not in await _agent_tools_of(setup)
    await _set_ceiling(setup.project_id, {"tools": {"scripts": "offer"}})
    assert (await _agent_tools_of(setup))["scripts"] == FOUR, "a ceiling that allows forbids nothing"


async def test_a_policy_stored_by_0_24_0_keeps_its_off_and_one_stored_now_says_nothing(
    setup: Fixture, deployment: Any
) -> None:
    """0.23.0 to 0.24.0 wrote the default `off` into every stored policy: it still says `off`. A policy saved
    now from a YAML that does not write `scripts` is stored without it, and the agent's default applies."""
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy
    from choregos_core.policy import parse_policy

    deployment(default=True)
    await _agent_with(setup, OFFERS)
    await _set_tools(setup.project_id, {"scripts": "off", "max_concurrent_calls": 4, "per_role": {}})
    assert "scripts" not in await _agent_tools_of(setup), "the stored `off` holds"
    async with session_scope() as session:
        row = await active_policy(session, setup.project_id)
        assert row is not None
        # What `PUT /projects/{id}/policy` stores (`routers/workflows.py`): the YAML, parsed and dumped.
        row.json_doc = parse_policy(row.yaml).model_dump(mode="json", exclude_none=True)
        assert "scripts" not in row.json_doc.get("tools", {}), "the YAML says nothing of scripts"
    assert (await _agent_tools_of(setup))["scripts"] == FOUR


async def test_an_mcp_cell_ignores_the_agent_s_default(setup: Fixture, deployment: Any) -> None:
    """An eval cell's door is the cell's (S57-09): the agent's `offer` does not reach an `mcp` cell."""
    deployment()
    await _eval_project(setup)
    await _set_org_choice(setup.project_id, "enabled")
    await _agent_with(setup, OFFERS)
    seeded = await evals.seed_eval_ticket({**_scripts_cell(), "tools": "mcp"})
    assert not seeded["skipped"], seeded["reason"]
    prepared = await stage_activities.prepare_stage({**seeded["plan"], "agent": AGENT})
    assert "scripts" not in prepared["stage_input"]["tools"]
    scripts = await evals.seed_eval_ticket(_scripts_cell())
    assert not scripts["skipped"], scripts["reason"]
    offered = await stage_activities.prepare_stage({**scripts["plan"], "agent": AGENT})
    assert offered["stage_input"]["tools"]["scripts"] == FOUR, "a `scripts` cell keeps its door"
