"""`POST /internal/runs/{id}/memory/search`: a run searches its resolved memories, and no others (S40-11b)."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_core.domain import Fact
from httpx import AsyncClient

from .agent_scopes_kit import ORG, make_project

URL = "/api/v1/internal/runs"
SEC = "org:varga#memory:security-lessons"
ARCH = "org:varga#memory:architecture-decisions"


@pytest.fixture(autouse=True)
def real_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_FAKES", "0")


def _memory() -> Any:
    from choregos_adapters import build

    return build("memory", "lexical", {"org": "varga"})


async def _setup(client: AsyncClient) -> None:
    project = await make_project(client, "billing-api")
    await client.put(f"/api/v1/projects/{project['id']}/connectors/memory", json={"type": "lexical"})
    await client.post(f"{ORG}/connectors", json={"name": "memory-org", "type": "lexical", "kind": "memory"})
    for slug in ("security-lessons", "architecture-decisions"):
        await client.post(f"{ORG}/memories", json={"slug": slug, "display_name": slug})
    memory = _memory()
    await memory.write_fact(SEC, Fact(kind="decision", subject="tls", content="pin the tls version"))
    await memory.write_fact(ARCH, Fact(kind="decision", subject="adr", content="a tls decision record"))
    await memory.write_fact(
        "billing-api", Fact(kind="decision", subject="shared-tls", content="tls in shared")
    )


async def _run(
    client: AsyncClient, run_id: str, memories: list[dict[str, str]] | None, **extra: Any
) -> dict[str, str]:
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from sqlalchemy import select

    async with session_scope() as session:
        project = (await session.execute(select(Project).where(Project.slug == "billing-api"))).scalar_one()
        item = WorkItem(project_id=project.id, tracker_key=f"B-{run_id}", title="T", state="ready")
        session.add(item)
        await session.flush()
        stage_input: dict[str, Any] = {} if memories is None else {"memories": memories}
        session.add(
            Run(
                id=run_id,
                work_item_id=item.id,
                project_id=project.id,
                stage_role="implement",
                status="running",
                stage_input={**stage_input, **extra},
            )
        )
        tracker = item.tracker_key
    token = mint_run_token(run_id, project_slug="billing-api", work_item_key=tracker, ttl_minutes=30)
    return {"Authorization": f"Bearer {token}"}


def _m(label: str, namespace: str, mode: str = "read") -> dict[str, str]:
    scope, slug = label.split(":")
    return {"label": label, "scope": scope, "slug": slug, "mode": mode, "namespace": namespace}


async def test_a_run_searches_every_memory_it_reads_and_names_each_hit(
    client: AsyncClient, admin: str
) -> None:
    await _setup(client)
    headers = await _run(
        client,
        "run-a",
        [
            _m("global:security-lessons", SEC),
            _m("global:architecture-decisions", ARCH),
            _m("project:shared", "billing-api", "write"),
        ],
    )
    answer = await client.post(f"{URL}/run-a/memory/search", json={"query": "tls", "k": 5}, headers=headers)
    assert answer.status_code == 200, answer.text
    assert [(h["memory"], h["subject"]) for h in answer.json()] == [
        ("global:security-lessons", "tls"),
        ("global:architecture-decisions", "adr"),
        ("project:shared", "shared-tls"),
    ]
    one = await client.post(
        f"{URL}/run-a/memory/search", json={"query": "tls", "memory": "project:shared"}, headers=headers
    )
    assert [h["memory"] for h in one.json()] == ["project:shared"]
    capped = await client.post(f"{URL}/run-a/memory/search", json={"query": "tls", "k": 2}, headers=headers)
    assert len(capped.json()) == 2


async def test_a_memory_the_run_does_not_read_does_not_exist_for_it(client: AsyncClient, admin: str) -> None:
    await _setup(client)
    headers = await _run(client, "run-b", [_m("global:security-lessons", SEC)])
    asked = await client.post(
        f"{URL}/run-b/memory/search",
        json={"query": "tls", "memory": "global:architecture-decisions"},
        headers=headers,
    )
    assert asked.status_code == 404, "bound by nobody: not in the run's StageInput"
    everything = await client.post(f"{URL}/run-b/memory/search", json={"query": "tls"}, headers=headers)
    assert {h["memory"] for h in everything.json()} == {"global:security-lessons"}, (
        "the shared memory is not read"
    )


async def test_another_runs_token_is_refused(client: AsyncClient, admin: str) -> None:
    await _setup(client)
    await _run(client, "run-c", [_m("global:security-lessons", SEC)])
    other = await _run(client, "run-d", [_m("global:security-lessons", SEC)])
    refused = await client.post(f"{URL}/run-c/memory/search", json={"query": "tls"}, headers=other)
    assert refused.status_code == 403, refused.text
    anonymous = await client.post(f"{URL}/run-c/memory/search", json={"query": "tls"})
    assert anonymous.status_code in {401, 403}


async def test_a_run_prepared_before_the_field_falls_back_to_its_legacy_scope(
    client: AsyncClient, admin: str
) -> None:
    await _setup(client)
    headers = await _run(client, "run-e", None, memory_scope="project")
    answer = await client.post(f"{URL}/run-e/memory/search", json={"query": "tls"}, headers=headers)
    assert [(h["memory"], h["subject"]) for h in answer.json()] == [("project:shared", "shared-tls")]
    none = await _run(client, "run-f", None, memory_scope="none")
    assert (await client.post(f"{URL}/run-f/memory/search", json={"query": "tls"}, headers=none)).json() == []


async def test_a_failing_memory_contributes_nothing_and_the_others_answer(
    client: AsyncClient, admin: str
) -> None:
    await _setup(client)
    headers = await _run(
        client,
        "run-g",
        [
            _m("global:security-lessons", SEC),
            _m("global:architecture-decisions", "org:varga#memory:vanished"),
        ],
    )
    answer = await client.post(f"{URL}/run-g/memory/search", json={"query": "tls"}, headers=headers)
    assert answer.status_code == 200
    assert [h["memory"] for h in answer.json()] == ["global:security-lessons"]
