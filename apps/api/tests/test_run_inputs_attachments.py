"""A step that declares `attachments` reads the files handed to its work item (S84-05, ADR 0197 §8)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_adapters.fakes import FakeObjectStore
from tranibor_contracts import artifact_digest

from .kits.artifacts import _run, _work_item

QUOTE_A = b"%PDF-1.4\n% Halden Industrial quote A\n%%EOF\n"
QUOTE_B = b"%PDF-1.4\n% Halden Industrial quote B\n%%EOF\n"
BASE = "/api/v1/internal/runs"


@pytest.fixture
def store() -> Iterator[FakeObjectStore]:
    from tranibor_api.object_store import use_object_store

    fake = FakeObjectStore()
    use_object_store(fake)
    yield fake
    use_object_store(None)


async def _hand(
    item: dict[str, Any], project: dict[str, Any], store: FakeObjectStore, name: str, data: bytes
) -> None:
    from tranibor_api.db.models import Project, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_api.services.artifacts import put_input_file, record_input_file

    async with session_scope() as session:
        work_item = await session.get(WorkItem, item["id"])
        owner = await session.get(Project, project["id"])
        assert work_item is not None and owner is not None
        stored = await put_input_file(
            store, org_id=owner.org_id, work_item_id=item["id"], name=name, type_="pdf", data=data
        )
        await record_input_file(session, org_id=owner.org_id, item=work_item, stored=stored, person="user-1")


async def test_a_step_declaring_attachments_lists_and_reads_every_handed_file(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _work_item(client, project)
    await _hand(item, project, store, "quote-a.pdf", QUOTE_A)
    await _hand(item, project, store, "quote-b.pdf", QUOTE_B)
    headers = await _run(item, project, "run-att", "compare", inputs=["attachments"])

    listed = await client.get(f"{BASE}/run-att/inputs", headers=headers)
    assert listed.status_code == 200, listed.text
    assert {(e["name"], e["filename"], e["digest"]) for e in listed.json()} == {
        ("quote-a.pdf", "quote-a.pdf", artifact_digest(QUOTE_A)),
        ("quote-b.pdf", "quote-b.pdf", artifact_digest(QUOTE_B)),
    }
    for name, data in (("quote-a.pdf", QUOTE_A), ("quote-b.pdf", QUOTE_B)):
        read = await client.get(f"{BASE}/run-att/inputs/{name}", headers=headers)
        assert read.status_code == 200 and read.content == data
    hand_offs = {
        h["output"]: h for h in (await client.get(f"/api/v1/work-items/{item['id']}/hand-offs")).json()
    }
    reads = [e["run_id"] for e in hand_offs["quote-a.pdf"]["events"] if e["kind"] == "read"]
    assert reads == ["run-att"], "reading a handed file leaves a read receipt, as for any input"
    assert (await client.get(f"{BASE}/run-att/inputs/attachments", headers=headers)).status_code == 404


async def test_a_step_that_does_not_declare_attachments_is_served_none_of_them(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _work_item(client, project)
    await _hand(item, project, store, "quote-a.pdf", QUOTE_A)
    headers = await _run(item, project, "run-plain", "compare", inputs=["spec_markdown"])
    assert (await client.get(f"{BASE}/run-plain/inputs", headers=headers)).json() == []
    assert (await client.get(f"{BASE}/run-plain/inputs/quote-a.pdf", headers=headers)).status_code == 404


async def test_a_handed_name_that_is_also_a_declared_input_is_listed_once_with_the_declared_revision(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.db.models import Project, Run, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_api.services.artifacts import store_file

    item = await _work_item(client, project)
    await _hand(item, project, store, "quote-a.pdf", QUOTE_A)
    await _hand(item, project, store, "quote-b.pdf", QUOTE_B)
    await _run(item, project, "run-prod", "write")
    revised = QUOTE_A + b"% revised by a step\n"
    async with session_scope() as session:
        work_item = await session.get(WorkItem, item["id"])
        run = await session.get(Run, "run-prod")
        owner = await session.get(Project, project["id"])
        assert work_item is not None and run is not None and owner is not None
        await store_file(
            session, store, org_id=owner.org_id, item=work_item, run=run, name="quote-a.pdf", type_="pdf",
            data=revised,
        )  # fmt: skip
    headers = await _run(item, project, "run-both", "compare", inputs=["quote-a.pdf", "attachments"])
    listed = (await client.get(f"{BASE}/run-both/inputs", headers=headers)).json()
    assert sorted(e["name"] for e in listed) == ["quote-a.pdf", "quote-b.pdf"]
    by_name = {e["name"]: e for e in listed}
    assert by_name["quote-a.pdf"]["digest"] == artifact_digest(revised), "the step's revision wins"
    served = await client.get(f"{BASE}/run-both/inputs/quote-a.pdf", headers=headers)
    assert served.content == revised
