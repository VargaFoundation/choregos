"""`GET /work-items/{id}/artifacts` and the inputs a runner mounts (S31-03, ADR 0049)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters.fakes import FakeObjectStore
from httpx import AsyncClient

SPEC = "# Spec\n\nDeduct credit notes from the total.\n"
DECK = b"PK\x03\x04" + b"slides" * 50
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def store() -> Iterator[FakeObjectStore]:
    from choregos_api.object_store import use_object_store

    fake = FakeObjectStore()
    use_object_store(fake)
    yield fake
    use_object_store(None)


async def _work_item(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Quarterly review", "start": False}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _run(
    item: dict[str, Any], project: dict[str, Any], run_id: str, role: str, *, inputs: list[str] | None = None
) -> dict[str, str]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=item["id"],
                project_id=project["id"],
                stage_role=role,
                status="running",
                agent_slug="presenter",
                agent_version=2,
                stage_input={"transition": {"outputs": [], "inputs": inputs or []}},
            )
        )
    token = mint_run_token(
        run_id, project_slug=project["slug"], work_item_key=item["tracker_key"], ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def _store_deck(
    item: dict[str, Any], project: dict[str, Any], store: FakeObjectStore, run_id: str
) -> Any:
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import store_file

    async with session_scope() as session:
        work_item = await session.get(WorkItem, item["id"])
        run = await session.get(Run, run_id)
        owner = await session.get(Project, project["id"])
        assert work_item is not None and run is not None and owner is not None
        row = await store_file(
            session, store, org_id=owner.org_id, item=work_item, run=run, name="deck", type_="pptx", data=DECK
        )
        return row.storage_key


async def _produce(
    client: AsyncClient, item: dict[str, Any], project: dict[str, Any], store: FakeObjectStore
) -> str:
    headers = await _run(item, project, "run-write", "write")
    result = {
        "schema": "choregos/StageResult/v1",
        "status": "done",
        "summary": "ok",
        "outputs": {"spec_markdown": SPEC},
    }
    posted = await client.post("/api/v1/internal/runs/run-write/result", headers=headers, json=result)
    assert posted.status_code == 200, posted.text
    key = await _store_deck(item, project, store, "run-write")
    assert isinstance(key, str)
    return key


async def test_the_artifacts_of_a_work_item_are_listed_and_served(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _work_item(client, project)
    await _produce(client, item, project, store)

    listed = await client.get(f"/api/v1/work-items/{item['id']}/artifacts")
    assert listed.status_code == 200, listed.text
    by_name = {a["name"]: a for a in listed.json()}
    assert set(by_name) == {"deck", "spec_markdown"}
    assert by_name["deck"]["media_type"] == PPTX and by_name["deck"]["size"] == len(DECK)
    assert by_name["deck"]["produced_by"] == {
        "run_id": "run-write",
        "stage": "write",
        "attempt": 1,
        "agent": "presenter",
        "agent_version": 2,
    }
    assert by_name["spec_markdown"]["storage_url"] is None

    one = await client.get(f"/api/v1/work-items/{item['id']}/artifacts/spec_markdown@1")
    assert one.status_code == 200 and one.json()["digest"] == _digest(SPEC.encode())
    assert (await client.get(f"/api/v1/work-items/{item['id']}/artifacts/spec_markdown@9")).status_code == 404
    assert (await client.get(f"/api/v1/work-items/{item['id']}/artifacts/nothing")).status_code == 404

    text = await client.get(f"/api/v1/work-items/{item['id']}/artifacts/spec_markdown/content")
    assert text.status_code == 200 and text.text == SPEC
    assert text.headers["content-type"].startswith("text/markdown")
    assert text.headers["x-choregos-digest"] == _digest(SPEC.encode())

    deck = await client.get(
        f"/api/v1/work-items/{item['id']}/artifacts/deck/content", params={"download": True}
    )
    assert deck.status_code == 200 and deck.content == DECK
    assert deck.headers["content-type"] == PPTX
    assert deck.headers["content-disposition"] == 'attachment; filename="deck.pptx"'
    assert "sandbox" in deck.headers["content-security-policy"]


async def test_content_altered_in_the_store_is_refused_not_served(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _work_item(client, project)
    key = await _produce(client, item, project, store)
    store.tamper(key, DECK + b"!")
    refused = await client.get(f"/api/v1/work-items/{item['id']}/artifacts/deck/content")
    assert refused.status_code == 409
    assert "no longer matches its digest" in refused.text
    assert DECK not in refused.content


async def test_a_run_lists_and_reads_its_declared_inputs_with_a_receipt(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _work_item(client, project)
    await _produce(client, item, project, store)
    headers = await _run(item, project, "run-read", "review", inputs=["deck", "spec_markdown", "absent"])

    listed = await client.get("/api/v1/internal/runs/run-read/inputs", headers=headers)
    assert listed.status_code == 200, listed.text
    assert {(e["name"], e["filename"]) for e in listed.json()} == {
        ("deck", "deck.pptx"),
        ("spec_markdown", "spec_markdown.md"),
    }, "an input nothing produced yet is simply absent"

    read = await client.get("/api/v1/internal/runs/run-read/inputs/deck", headers=headers)
    assert read.status_code == 200 and read.content == DECK
    assert (
        await client.get("/api/v1/internal/runs/run-read/inputs/spec_markdown", headers=headers)
    ).text == SPEC
    undeclared = await client.get("/api/v1/internal/runs/run-write/inputs/deck", headers=headers)
    assert undeclared.status_code in {403, 404}

    hand_offs = {
        h["output"]: h for h in (await client.get(f"/api/v1/work-items/{item['id']}/hand-offs")).json()
    }
    reads = [(e["run_id"], e["digest"]) for e in hand_offs["deck"]["events"] if e["kind"] == "read"]
    assert reads == [("run-read", _digest(DECK))], "the read is receipted under the file's digest"
    assert any(
        e["kind"] == "read" and e["run_id"] == "run-read" for e in hand_offs["spec_markdown"]["events"]
    )
