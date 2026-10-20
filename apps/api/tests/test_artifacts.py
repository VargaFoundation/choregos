"""Artifacts are typed, stored and traced (S31-01, ADR 0049).

Every output a step leaves becomes a revision with a type, a media type, a size, the sha256 of its
bytes and the run that produced it. A file goes to the object store. Every read recomputes the
digest, and bytes that no longer match are refused.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from choregos_adapters.fakes import FakeObjectStore
from httpx import AsyncClient

SPEC = "# Spec\n\nDeduct credit notes from the total.\n"
PROFILES = [{"name": "Ada", "score": 9}, {"name": "Linus", "score": 7}]


async def _work_item(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Credit notes", "start": False}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _run(
    item: dict[str, Any],
    project: dict[str, Any],
    run_id: str,
    role: str,
    *,
    outputs: list[str] | None = None,
    output_types: dict[str, str] | None = None,
    agent: tuple[str, int] | None = None,
    attempt: int = 1,
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
                attempt=attempt,
                status="running",
                agent_slug=agent[0] if agent else None,
                agent_version=agent[1] if agent else None,
                stage_input={"transition": {"outputs": outputs or [], "output_types": output_types or {}}},
            )
        )
    token = mint_run_token(
        run_id, project_slug=project["slug"], work_item_key=item["tracker_key"], ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def _post(client: AsyncClient, run_id: str, headers: dict[str, str], outputs: dict[str, Any]) -> None:
    result = {"schema": "choregos/StageResult/v1", "status": "done", "summary": "done", "outputs": outputs}
    posted = await client.post(f"/api/v1/internal/runs/{run_id}/result", headers=headers, json=result)
    assert posted.status_code == 200, posted.text


async def _rows(item_id: str) -> list[Any]:
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import artifacts_of

    async with session_scope() as session:
        return await artifacts_of(session, item_id)


async def test_each_output_becomes_a_typed_artifact_traced_to_its_run(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _work_item(client, project)
    headers = await _run(item, project, "run-refine", "refine", outputs=["profiles"], agent=("analyst", 3))
    await _post(client, "run-refine", headers, {"spec_markdown": SPEC, "profiles": PROFILES, "size": "S"})

    rows = {row.name: row for row in await _rows(item["id"])}
    assert set(rows) == {"spec_markdown", "profiles"}, (
        "a non-declared, non-document output is not an artifact"
    )
    spec = rows["spec_markdown"]
    assert (spec.type, spec.kind, spec.media_type, spec.version) == (
        "markdown",
        "markdown",
        "text/markdown",
        1,
    )
    assert spec.digest == "sha256:" + hashlib.sha256(SPEC.encode()).hexdigest()
    assert spec.size == len(SPEC.encode())
    assert (spec.run_id, spec.stage, spec.attempt, spec.agent_slug, spec.agent_version) == (
        "run-refine",
        "refine",
        1,
        "analyst",
        3,
    )
    assert spec.storage_url is None, "an inline artifact stays in the database"
    assert (rows["profiles"].type, rows["profiles"].media_type) == ("json", "application/json")


async def test_json_written_as_text_is_stored_as_the_document_it_spells(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """RESEARCH-2 on dev (S38-13): the model answered `strategies` as a JSON string, the gate
    validated it parsed, the artifact was typed markdown, and the backtest found no
    `strategies.json`. Declared `json`, text that parses is that document, not a JSON string."""
    item = await _work_item(client, project)
    headers = await _run(
        item,
        project,
        "run-strategize",
        "strategize",
        outputs=["strategies", "notes"],
        output_types={"strategies": "json", "notes": "json"},
    )
    strategies = {"strategies": [{"id": "s1", "family": "sma_cross", "params": {"fast": 20, "slow": 100}}]}
    outputs = {"strategies": json.dumps(strategies), "notes": "not json"}
    await _post(client, "run-strategize", headers, outputs)

    rows = {row.name: row for row in await _rows(item["id"])}
    assert (rows["strategies"].type, rows["strategies"].media_type) == ("json", "application/json")
    assert rows["strategies"].content == strategies, "the document, not the string that spells it"
    assert rows["notes"].content == "not json", "text that does not parse is kept as written"


async def test_a_new_content_is_a_new_version_and_the_same_content_is_not(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _work_item(client, project)
    first = await _run(item, project, "run-1", "refine")
    await _post(client, "run-1", first, {"spec_markdown": SPEC})
    # A retry that writes the same specification makes no new version.
    again = await _run(item, project, "run-2", "refine", attempt=2)
    await _post(client, "run-2", again, {"spec_markdown": SPEC})
    assert [(r.version, r.run_id) for r in await _rows(item["id"])] == [(1, "run-1")]

    revised = await _run(item, project, "run-3", "refine", attempt=3)
    await _post(client, "run-3", revised, {"spec_markdown": SPEC + "\nRound half to even.\n"})
    assert [(r.version, r.run_id) for r in await _rows(item["id"])] == [(1, "run-1"), (2, "run-3")]


async def test_an_inline_artifact_edited_behind_the_platform_is_refused_on_read(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import ArtifactCorruptedError, read_content

    item = await _work_item(client, project)
    headers = await _run(item, project, "run-refine", "refine")
    await _post(client, "run-refine", headers, {"spec_markdown": SPEC})
    store = FakeObjectStore()
    (row,) = await _rows(item["id"])
    assert await read_content(store, row) == SPEC.encode()

    async with session_scope() as session:
        from choregos_api.db.models import ArtifactRecord

        stored = await session.get(ArtifactRecord, row.id)
        assert stored is not None
        stored.content = SPEC.replace("credit notes", "nothing")
    (row,) = await _rows(item["id"])
    with pytest.raises(ArtifactCorruptedError, match="no longer matches its digest"):
        await read_content(store, row)


async def test_a_file_goes_to_the_object_store_once_and_is_checked_on_read(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import ArtifactCorruptedError, read_content, store_file

    item = await _work_item(client, project)
    await _run(item, project, "run-present", "present", agent=("presenter", 1))
    deck = b"PK\x03\x04" + b"slides" * 100
    store = FakeObjectStore()
    async with session_scope() as session:
        work_item = await session.get(WorkItem, item["id"])
        run = await session.get(Run, "run-present")
        assert work_item is not None and run is not None
        work_item_org = (await session.get(Project, project["id"])).org_id  # type: ignore[union-attr]
        for _ in range(2):  # a replayed upload writes nothing new
            row = await store_file(
                session,
                store,
                org_id=work_item_org,
                item=work_item,
                run=run,
                name="deck",
                type_="pptx",
                data=deck,
            )
    assert store.puts == 1
    assert row.version == 1 and row.size == len(deck)
    assert row.media_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert row.digest == "sha256:" + hashlib.sha256(deck).hexdigest()
    assert row.storage_key is not None and row.storage_key.endswith(row.digest.removeprefix("sha256:"))
    assert row.storage_url == f"memory://choregos/{row.storage_key}"
    assert await read_content(store, row) == deck

    store.tamper(row.storage_key, deck + b"!")
    with pytest.raises(ArtifactCorruptedError):
        await read_content(store, row)


async def test_a_name_that_cannot_be_a_file_name_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import store_file

    item = await _work_item(client, project)
    await _run(item, project, "run-x", "present")
    async with session_scope() as session:
        work_item = await session.get(WorkItem, item["id"])
        run = await session.get(Run, "run-x")
        assert work_item is not None and run is not None
        work_item_org = (await session.get(Project, project["id"])).org_id  # type: ignore[union-attr]
        with pytest.raises(ValueError, match="refused"):
            await store_file(
                session,
                FakeObjectStore(),
                org_id=work_item_org,
                item=work_item,
                run=run,
                name="../escape",
                type_="png",
                data=b"x",
            )
