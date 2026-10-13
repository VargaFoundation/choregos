"""The demo organisation of the local install (`python -m choregos_api.local_demo`, S28-01)."""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
import yaml
from choregos_api.config import Settings
from choregos_api.local_demo import (
    DEMO_WORKFLOW,
    demo_connectors,
    litellm_config,
    model_provider,
    read_provider,
    seed_local_demo,
    write_litellm_config,
)
from choregos_core.dsl.parser import parse_workflow
from httpx import AsyncClient

from .conftest import login


def _settings() -> Settings:
    return Settings(bootstrap_org="demo", bootstrap_org_name="Demo")


def test_the_demo_workflow_is_valid() -> None:
    workflow, report = parse_workflow(DEMO_WORKFLOW, strict=True)
    assert report.valid, report.errors
    assert workflow.metadata.name == "local-demo"


def test_fakes_mode_simulates_the_agent_and_keeps_the_tracker_inside_choregos() -> None:
    connectors = demo_connectors()
    assert connectors["runtime"] == ("fake", {"simulate": True})
    assert connectors["tracker"] == ("internal", {})


async def test_the_seed_creates_the_project_once(app: Any) -> None:
    first = await seed_local_demo(_settings())
    assert first["organisations"] == ["demo"]
    assert first["projects"] == ["demo"]
    assert sorted(first["connectors"]) == sorted(demo_connectors())

    again = await seed_local_demo(_settings())
    assert again == {"organisations": [], "projects": [], "connectors": [], "switched": [], "work_items": []}


async def test_a_request_created_in_the_demo_project_starts_its_workflow(
    app: Any, client: AsyncClient
) -> None:
    from choregos_api.temporal import get_temporal

    await seed_local_demo(_settings())
    await login(client, "admin@varga.dev")  # a dev admin of every organisation of the bench
    created = await client.post(
        "/api/v1/projects/demo:demo/work-items", json={"title": "Plan the team offsite", "body": "Lyon, May."}
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert (item["workflow_name"], item["state"]) == ("local-demo", "inbox")
    assert item["temporal_wf_id"] in get_temporal().started  # type: ignore[attr-defined]


# ───────────────────────── S28-02: a real model when `.env` has a key ─────────────────────────


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, None),
        ({"ANTHROPIC_API_KEY": "sk-ant-x"}, "anthropic"),
        ({"OPENAI_API_KEY": "sk-x"}, "openai"),
        ({"ANTHROPIC_API_KEY": "sk-ant-x", "OPENAI_API_KEY": "sk-x"}, "anthropic"),
        ({"ANTHROPIC_API_KEY": "  "}, None),
        ({"ANTHROPIC_API_KEY": "sk-ant-x", "LITELLM_REAL": "0"}, None),
        ({"OPENAI_API_KEY": "sk-x", "LITELLM_REAL": "1"}, "openai"),
    ],
)
def test_the_key_in_env_decides_the_mode(env: dict[str, str], expected: str | None) -> None:
    assert model_provider(env) == expected


def test_litellm_real_without_a_key_refuses_instead_of_running_fakes() -> None:
    with pytest.raises(ValueError, match="LITELLM_REAL=1"):
        model_provider({"LITELLM_REAL": "1"})


def test_the_key_reaches_the_model_by_reference_never_by_value(tmp_path: pathlib.Path) -> None:
    provider = write_litellm_config(tmp_path, {"ANTHROPIC_API_KEY": "sk-ant-secret"})
    written = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "sk-ant-secret" not in written
    models = {m["model_name"]: m["litellm_params"] for m in yaml.safe_load(written)["model_list"]}
    assert models["platform/standard"]["model"].startswith("anthropic/claude-")
    assert models["platform/standard"]["api_key"] == "os.environ/ANTHROPIC_API_KEY"
    assert "mock_response" not in models["platform/standard"]
    assert read_provider(tmp_path / "provider") == provider == "anthropic"


def test_fakes_mode_mocks_every_alias() -> None:
    for model in litellm_config(None)["model_list"]:
        assert model["litellm_params"]["mock_response"]
    assert read_provider(pathlib.Path("/nonexistent/provider")) is None


async def test_adding_a_key_switches_the_demo_project_to_a_real_agent(app: Any) -> None:
    from choregos_api.db.models import Connector, Project
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await seed_local_demo(_settings())
    switched = await seed_local_demo(_settings(), "openai")
    assert sorted(switched["switched"]) == ["agent", "gateway", "models", "runtime"]
    async with session_scope(orgs="*") as session:
        project = (await session.execute(select(Project).where(Project.slug == "demo"))).scalar_one()
        rows = {
            c.kind: (c.type, c.config)
            for c in (
                await session.execute(select(Connector).where(Connector.project_id == project.id))
            ).scalars()
        }
        assert project.config["agent"]["default_backend"] == "opencode"
        assert project.config["models"]["profiles"]["standard"] == "openai/gpt-5.5"
    assert rows["runtime"] == ("local_docker", {"network": "choregos"})
    assert rows["gateway"] == ("litellm", {})
    assert rows == demo_connectors("openai")


async def test_a_connector_changed_by_a_person_is_not_switched_back(app: Any) -> None:
    from choregos_api.db.models import Connector
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await seed_local_demo(_settings())
    async with session_scope(orgs="*") as session:
        row = (await session.execute(select(Connector).where(Connector.kind == "runtime"))).scalar_one()
        row.type, row.config = "k8s_job", {}
    report = await seed_local_demo(_settings(), "anthropic")
    assert "runtime" not in report["switched"]
    async with session_scope(orgs="*") as session:
        row = (await session.execute(select(Connector).where(Connector.kind == "runtime"))).scalar_one()
        assert row.type == "k8s_job"


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_with_a_key_the_agent_backend_accepts_the_model_litellm_serves(provider: Any) -> None:
    """Seen on 2026-10-12 with the published runner: `claude-code` refused `platform/standard` (it
    cannot see the Claude behind the alias), then asked LiteLLM for a model the run's key did not
    allow. The demo project runs `opencode` and names the provider's model, which LiteLLM serves."""
    from choregos_api.local_demo import demo_backend, demo_models
    from choregos_contracts import ModelRef
    from choregos_runner.backends import get_backend

    model = demo_models(provider)["standard"]
    assert demo_backend(provider) == "opencode"
    get_backend("opencode").validate_model(
        ModelRef(litellm_model=model, base_url="http://litellm:4000", api_format="openai")
    )
    served = {m["model_name"] for m in litellm_config(provider)["model_list"]}
    assert model in served and "platform/standard" in served


# ───────────────────────── S28-03: the first sign-in lands on a work item ─────────────────────────


async def test_the_first_sign_in_finds_the_demo_project_with_one_running_work_item(
    app: Any, client: AsyncClient
) -> None:
    from choregos_api.temporal import get_temporal

    report = await seed_local_demo(_settings())
    assert report["work_items"] == ["DEMO-1"]
    await login(client, "admin@varga.dev")
    projects = (await client.get("/api/v1/orgs/demo/projects")).json()["items"]
    assert [p["slug"] for p in projects] == ["demo"]
    items = (await client.get("/api/v1/projects/demo:demo/work-items")).json()["items"]
    assert [(i["tracker_key"], i["state"]) for i in items] == [("DEMO-1", "inbox")]
    assert items[0]["temporal_wf_id"] in get_temporal().started  # type: ignore[attr-defined]
    assert (await seed_local_demo(_settings()))["work_items"] == [], "one work item, not one per start"


async def test_with_a_model_key_the_demo_work_item_waits_to_be_started(app: Any) -> None:
    """A real model costs money: the install does not spend it before anyone has opened the console."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import get_temporal
    from sqlalchemy import select

    assert (await seed_local_demo(_settings(), "anthropic"))["work_items"] == ["DEMO-1"]
    async with session_scope(orgs="*") as session:
        item = (await session.execute(select(WorkItem))).scalar_one()
    assert item.temporal_wf_id is None
    assert not get_temporal().started  # type: ignore[attr-defined]


async def test_development_sign_in_is_refused_outside_a_development_environment() -> None:
    """The compose turns development sign-in on; the API refuses it in staging and prod (config.py)."""
    import pydantic

    for env in ("staging", "prod"):
        with pytest.raises(pydantic.ValidationError, match="DEV_LOGIN_ENABLED"):
            Settings(env=env, dev_login_enabled=True)
    assert Settings(env="dev", dev_login_enabled=True).dev_login_enabled
