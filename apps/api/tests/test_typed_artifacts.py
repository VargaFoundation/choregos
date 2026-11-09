"""A typed output is checked, snapshotted and readable in one list (ADR 0129 §6, §9 to §11)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .kits.artifacts import _post, _rows, _run, _work_item

SEVERITY = {
    "type": "level",
    "show": "facts",
    "title": "Severity",
    "levels": [{"value": "sev3", "tone": "warn"}, {"value": "sev1", "tone": "danger"}],
}
COVERAGE = {"type": "metric", "show": "facts", "unit": "percent", "min": 0, "max": 100, "better": "higher"}


async def _declared(
    item: dict[str, Any], project: dict[str, Any], run_id: str, displays: dict[str, Any]
) -> dict[str, str]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    headers = await _run(item, project, run_id, "triage", outputs=list(displays))
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        run.stage_input = {"transition": {"outputs": list(displays), "artifact_displays": displays}}
    return headers


async def _listed(client: AsyncClient, item: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(f"/api/v1/work-items/{item['id']}/artifacts")
    assert response.status_code == 200, response.text
    return {a["name"]: a for a in response.json()}


async def test_a_declared_level_is_recorded_as_a_level_and_snapshotted(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _work_item(client, project)
    headers = await _declared(item, project, "r1", {"severity": SEVERITY})
    await _post(client, "r1", headers, {"severity": "SEV1"})
    [row] = await _rows(item["id"])
    assert (row.type, row.kind, row.media_type, row.content) == ("level", "value", "text/plain", "sev1")
    assert row.display["show"] == "facts"
    assert row.display["type"] == "level"


async def test_the_list_carries_a_value_and_how_it_is_shown(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _work_item(client, project)
    headers = await _declared(item, project, "r1", {"severity": SEVERITY, "coverage": COVERAGE})
    await _post(client, "r1", headers, {"severity": "sev3", "coverage": "87%"})
    listed = await _listed(client, item)
    assert listed["severity"]["value"] == "sev3"
    assert [level["tone"] for level in listed["severity"]["display"]["levels"]] == ["warn", "danger"]
    assert listed["coverage"]["value"] == 87
    assert listed["coverage"]["display"]["unit"] == "percent"


async def test_a_value_changed_behind_the_platform_leaves_the_list(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import ArtifactRecord
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    item = await _work_item(client, project)
    headers = await _declared(item, project, "r1", {"severity": SEVERITY})
    await _post(client, "r1", headers, {"severity": "sev3"})
    async with session_scope() as session:
        await session.execute(
            update(ArtifactRecord).where(ArtifactRecord.work_item_id == item["id"]).values(content="sev1")
        )
    listed = await _listed(client, item)
    assert listed["severity"]["value"] is None
    assert listed["severity"]["display"] is None


async def test_a_value_an_unsupervised_agent_got_wrong_is_kept_plain(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _work_item(client, project)
    headers = await _declared(item, project, "r1", {"severity": SEVERITY})
    await _post(client, "r1", headers, {"severity": "catastrophic"})
    [row] = await _rows(item["id"])
    assert (row.type, row.content, row.display) == ("markdown", "catastrophic", None)
    assert (await _listed(client, item))["severity"]["value"] is None


async def test_a_platform_output_is_typed_without_any_declaration(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _work_item(client, project)
    headers = await _run(item, project, "r1", "triage", outputs=["size", "risk"])
    await _post(client, "r1", headers, {"size": "S", "risk": "low"})
    rows = {row.name: row for row in await _rows(item["id"])}
    assert (rows["size"].type, rows["size"].content) == ("level", "S")
    listed = await _listed(client, item)
    assert listed["risk"]["value"] == "low"
    assert listed["risk"]["display"]["levels"][0] == {"value": "low", "tone": "ok", "title": None}


async def test_a_revision_older_than_the_display_is_read_by_its_name(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import ArtifactRecord
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    item = await _work_item(client, project)
    headers = await _run(item, project, "r1", "triage", outputs=["size"])
    await _post(client, "r1", headers, {"size": "S"})
    async with session_scope() as session:
        await session.execute(
            update(ArtifactRecord)
            .where(ArtifactRecord.work_item_id == item["id"])
            .values(type="markdown", kind="markdown", media_type="text/markdown", display=None)
        )
    listed = await _listed(client, item)
    assert listed["size"]["type"] == "markdown", "its row is not rewritten"
    assert listed["size"]["display"]["type"] == "level"
    assert listed["size"]["value"] == "S"


async def test_a_place_without_a_type_keeps_the_type_of_the_value(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _work_item(client, project)
    headers = await _declared(item, project, "r1", {"notes": {"show": "hidden", "title": "Notes"}})
    await _post(client, "r1", headers, {"notes": "# Notes"})
    [row] = await _rows(item["id"])
    assert row.type == "markdown"
    assert row.display == {"type": "markdown", "show": "hidden", "title": "Notes"}


async def test_a_value_converted_to_its_type_still_credits_its_producer(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import producing_runs

    item = await _work_item(client, project)
    headers = await _declared(item, project, "r1", {"severity": SEVERITY})
    await _post(client, "r1", headers, {"severity": "SEV1"})
    async with session_scope() as session:
        runs = await producing_runs(session, item["id"], ["severity"], {"severity": "SEV1"}, reader="r2")
    assert runs == ["r1"]


async def test_a_file_keeps_the_place_its_workflow_gave_it(
    client: AsyncClient, project: dict[str, Any], monkeypatch: Any
) -> None:
    from choregos_adapters.fakes import FakeObjectStore
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import store_file

    item = await _work_item(client, project)
    await _declared(item, project, "r1", {"audit": {"show": "hidden", "title": "Audit"}})
    async with session_scope() as session:
        run = await session.get(Run, "r1")
        work_item = await session.get(WorkItem, item["id"])
        owner = await session.get(Project, project["id"])
        assert run is not None and work_item is not None and owner is not None
        row = await store_file(
            session,
            FakeObjectStore(),
            org_id=owner.org_id,
            item=work_item,
            run=run,
            name="audit",
            type_="pdf",
            data=b"%PDF",
        )
        assert row.display == {"type": "pdf", "show": "hidden", "title": "Audit"}


async def test_a_reader_of_a_converted_value_keeps_its_receipt(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import value_for_receipt

    item = await _work_item(client, project)
    headers = await _declared(item, project, "r1", {"severity": SEVERITY})
    await _post(client, "r1", headers, {"severity": "SEV1"})
    async with session_scope():
        [row] = await _rows(item["id"])
    assert value_for_receipt(row, {"severity": "SEV1"}) == "SEV1", "the produced receipt hashed this"
    assert value_for_receipt(row, {"severity": "edited by a person"}) == "sev1"
    assert value_for_receipt(row, {}) == "sev1"


def test_a_level_travels_over_a2a_as_text_not_a_file() -> None:
    from types import SimpleNamespace

    from choregos_api.a2a.door import _content_part

    row: Any = SimpleNamespace(storage_key=None, type="level", content="approve")
    assert _content_part(row, b"approve") == {"kind": "text", "text": "approve"}
