"""`PUT /internal/runs/{id}/outputs/{name}`: a runner hands over a deliverable (S31-05, ADR 0049)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters.fakes import FakeObjectStore
from httpx import AsyncClient

DECK = b"PK\x03\x04" + b"slides" * 40
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DIGEST = "sha256:" + hashlib.sha256(DECK).hexdigest()


@pytest.fixture
def store() -> Iterator[FakeObjectStore]:
    from choregos_api.object_store import use_object_store

    fake = FakeObjectStore()
    use_object_store(fake)
    yield fake
    use_object_store(None)


async def _run(
    client: AsyncClient, project: dict[str, Any], run_id: str
) -> tuple[dict[str, Any], dict[str, str]]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Board deck", "start": False}
    )
    assert created.status_code == 201, created.text
    item = dict(created.json())
    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=item["id"],
                project_id=project["id"],
                stage_role="present",
                status="running",
                stage_input={"transition": {"outputs": ["deck"], "output_types": {"deck": "pptx"}}},
            )
        )
    token = mint_run_token(
        run_id, project_slug=project["slug"], work_item_key=item["tracker_key"], ttl_minutes=5
    )
    return item, {"Authorization": f"Bearer {token}"}


def _put(
    client: AsyncClient, run_id: str, headers: dict[str, str], data: bytes, digest: str, type_: str
) -> Any:
    return client.put(
        f"/api/v1/internal/runs/{run_id}/outputs/deck",
        params={"type": type_},
        content=data,
        headers={**headers, "X-Choregos-Digest": digest, "Content-Type": "application/octet-stream"},
    )


async def test_a_deliverable_is_stored_typed_and_receipted(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item, headers = await _run(client, project, "run-present")
    stored = await _put(client, "run-present", headers, DECK, DIGEST, "pptx")
    assert stored.status_code == 200, stored.text
    artifact = stored.json()
    assert (
        artifact["name"],
        artifact["type"],
        artifact["media_type"],
        artifact["digest"],
        artifact["size"],
    ) == (
        "deck",
        "pptx",
        PPTX,
        DIGEST,
        len(DECK),
    )
    assert artifact["produced_by"]["run_id"] == "run-present"
    again = await _put(client, "run-present", headers, DECK, DIGEST, "pptx")
    assert again.json()["version"] == 1 and store.puts == 1, "the same bytes make no new version"

    served = await client.get(f"/api/v1/work-items/{item['id']}/artifacts/deck/content")
    assert served.content == DECK and served.headers["content-type"] == PPTX
    hand_offs = {
        h["output"]: h for h in (await client.get(f"/api/v1/work-items/{item['id']}/hand-offs")).json()
    }
    assert hand_offs["deck"]["current_digest"] == DIGEST
    assert [(e["kind"], e["digest"]) for e in hand_offs["deck"]["events"]] == [("produced", DIGEST)]


async def test_bytes_that_do_not_match_the_announced_digest_are_refused(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    _, headers = await _run(client, project, "run-present")
    refused = await _put(client, "run-present", headers, DECK + b"!", DIGEST, "pptx")
    assert refused.status_code == 422
    assert store.puts == 0
    unknown = await _put(client, "run-present", headers, DECK, DIGEST, "keynote")
    assert unknown.status_code == 422


async def test_too_large_or_too_late_is_refused(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import get_settings

    _, headers = await _run(client, project, "run-present")
    monkeypatch.setattr(get_settings(), "artifact_max_bytes", 16)
    assert (await _put(client, "run-present", headers, DECK, DIGEST, "pptx")).status_code == 413
    monkeypatch.undo()

    result = {"schema": "choregos/StageResult/v1", "status": "failed", "summary": "gave up", "reason": "x"}
    posted = await client.post("/api/v1/internal/runs/run-present/result", headers=headers, json=result)
    assert posted.status_code == 200
    assert (await _put(client, "run-present", headers, DECK, DIGEST, "pptx")).status_code == 409
