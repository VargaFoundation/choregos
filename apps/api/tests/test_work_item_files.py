"""`POST /work-items/{id}/files`: a person hands files to a work item, a start waits (S84-04, ADR 0197)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from tranibor_adapters.fakes import FakeObjectStore
from tranibor_contracts import artifact_digest

from .conftest import login

PDF = b"%PDF-1.4\n% Halden Industrial sample quote\n%%EOF\n"
OTHER_PDF = b"%PDF-1.4\n% Halden Industrial another quote\n%%EOF\n"


def _xlsx() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    return out.getvalue()


@pytest.fixture
def store() -> Iterator[FakeObjectStore]:
    from tranibor_api.object_store import use_object_store

    fake = FakeObjectStore()
    use_object_store(fake)
    yield fake
    use_object_store(None)


async def _publish(client: AsyncClient, project: dict[str, Any], attachments: dict[str, Any] | None) -> None:
    from tranibor_core.dsl import template_yaml

    tracker = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    document = yaml.safe_load(template_yaml("default-simple"))
    if attachments is not None:
        document["metadata"]["attachments"] = attachments
    published = await client.put(
        f"/api/v1/projects/{project['id']}/workflow", json={"yaml": yaml.safe_dump(document, sort_keys=False)}
    )
    assert published.status_code == 200, published.text


async def _item(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Compare the quotes", "start": False}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _ready(client: AsyncClient, project: dict[str, Any], **attachments: Any) -> dict[str, Any]:
    await _publish(client, project, {"accept": ["pdf", "xlsx"], **attachments})
    return await _item(client, project)


def _send(item: dict[str, Any], *files: tuple[str, bytes]) -> dict[str, Any]:
    return {
        "url": f"/api/v1/work-items/{item['id']}/files",
        "files": [("files", (name, data, "application/octet-stream")) for name, data in files],
    }


async def _hand(client: AsyncClient, item: dict[str, Any], *files: tuple[str, bytes]) -> Any:
    return await client.post(**_send(item, *files))


async def _rows() -> list[Any]:
    from tranibor_api.db.models import ArtifactRecord
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        return list((await session.execute(select(ArtifactRecord).order_by(ArtifactRecord.name))).scalars())


async def test_a_pdf_is_stored_and_listed_as_handed_by_a_person(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _ready(client, project)
    answer = await _hand(client, item, ("Quote A.pdf", PDF))
    assert answer.status_code == 201, answer.text
    [stored] = answer.json()
    assert (stored["name"], stored["type"], stored["version"]) == ("Quote-A.pdf", "pdf", 1)
    assert stored["handed_by"] == "person"
    assert stored["digest"] == artifact_digest(PDF) and stored["size"] == len(PDF)
    assert stored["produced_by"]["run_id"].startswith("person:")
    listed = (await client.get(f"/api/v1/work-items/{item['id']}/artifacts")).json()
    assert [(a["name"], a["handed_by"]) for a in listed] == [("Quote-A.pdf", "person")]
    content = await client.get(f"/api/v1/work-items/{item['id']}/artifacts/Quote-A.pdf/content")
    assert content.content == PDF, "the bytes come back, digest checked"
    assert [data for data, _ in store.objects.values()] == [PDF]


async def test_a_file_from_an_application_is_listed_as_a2a_and_a_step_output_as_null(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.db.models import Project, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_api.services.artifacts import (
        handed,
        handed_artifacts,
        put_input_file,
        record_input_file,
        to_contract,
    )

    item = await _ready(client, project)
    assert (await _hand(client, item, ("a.pdf", PDF))).status_code == 201
    async with session_scope() as session:
        work_item = await session.get(WorkItem, item["id"])
        owner = await session.get(Project, project["id"])
        assert work_item is not None and owner is not None
        stored = await put_input_file(
            store, org_id=owner.org_id, work_item_id=item["id"], name="b.pdf", type_="pdf", data=OTHER_PDF
        )
        row = await record_input_file(session, org_id=owner.org_id, item=work_item, stored=stored)
        assert handed(row) == "a2a" and to_contract(row).handed_by == "a2a"
        names = [r.name for r in await handed_artifacts(session, item["id"])]
    assert names == ["a.pdf", "b.pdf"], "both producers are handed files"


async def test_a_type_outside_accept_is_refused(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _ready(client, project, accept=["pdf"])
    answer = await _hand(client, item, ("sheet.xlsx", _xlsx()))
    assert answer.status_code == 422 and "accepts: pdf" in answer.json()["detail"]
    assert await _rows() == [] and not store.objects


async def test_a_csv_is_taken_as_text_and_a_binary_csv_or_an_unchecked_type_is_refused(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _ready(client, project, accept=["pdf", "csv", "png", "mp4"])
    taken = await _hand(client, item, ("prices.csv", b"item;qty;unit price\npallet jack;3;410,00\n"))
    assert taken.status_code == 201, taken.text
    assert [row["name"] for row in taken.json()] == ["prices.csv"]
    for name, data in (
        ("binary.csv", b"item\0qty"),
        ("logo.png", b"GIF89a not a png"),
        ("clip.mp4", b"\0\0\0 ftyp"),
    ):
        refused = await _hand(client, item, (name, data))
        assert refused.status_code == 422, (name, refused.text)
    assert [row.name for row in await _rows()] == ["prices.csv"]


async def test_a_workflow_without_attachments_takes_no_file(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    await _publish(client, project, None)
    item = await _item(client, project)
    answer = await _hand(client, item, ("a.pdf", PDF))
    assert answer.status_code == 422 and "takes no file" in answer.json()["detail"]


async def test_bytes_that_are_not_their_extension_are_refused(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _ready(client, project)
    for name, data in (("fake.pdf", b"not a pdf at all"), ("fake.xlsx", b"%PDF-1.4 but called a sheet")):
        answer = await _hand(client, item, (name, data))
        assert answer.status_code == 422, answer.text
    assert await _rows() == [] and not store.objects


async def test_a_file_over_the_project_limit_answers_413(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.db.models import Project
    from tranibor_api.db.session import session_scope

    item = await _ready(client, project)
    async with session_scope() as session:
        owner = await session.get(Project, project["id"])
        assert owner is not None
        owner.config = {**owner.config, "a2a": {"max_file_bytes": len(PDF)}}
    assert (await _hand(client, item, ("fits.pdf", PDF))).status_code == 201
    answer = await _hand(client, item, ("big.pdf", PDF + b"x"))
    assert answer.status_code == 413 and "big.pdf" in answer.json()["detail"]
    assert [r.name for r in await _rows()] == ["fits.pdf"]


async def test_the_count_of_distinct_names_stays_within_max(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _ready(client, project, max=20)
    first = [(f"q{n:02}.pdf", PDF + str(n).encode()) for n in range(20)]
    assert (await _hand(client, item, *first)).status_code == 201
    over = await _hand(client, item, ("q20.pdf", PDF))
    assert over.status_code == 422 and "21 distinct files" in over.json()["detail"]
    again = await _hand(client, item, ("q00.pdf", OTHER_PDF))
    assert again.status_code == 201, "a new revision of a known name is not a 21st name"
    assert len(await _rows()) == 21


async def test_the_same_name_twice_is_revision_two_and_the_same_bytes_none(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _ready(client, project)
    assert (await _hand(client, item, ("quote.pdf", PDF))).json()[0]["version"] == 1
    assert (await _hand(client, item, ("quote.pdf", PDF))).json()[0]["version"] == 1, "no new revision"
    assert (await _hand(client, item, ("quote.pdf", OTHER_PDF))).json()[0]["version"] == 2
    assert [(r.name, r.version) for r in await _rows()] == [("quote.pdf", 1), ("quote.pdf", 2)]


async def test_two_names_that_slug_to_one_are_refused_together(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    item = await _ready(client, project)
    answer = await _hand(client, item, ("my quote.pdf", PDF), ("my-quote.pdf", OTHER_PDF), ("fine.pdf", PDF))
    assert answer.status_code == 422 and "same name" in answer.json()["detail"], answer.text
    assert await _rows() == [] and not store.objects, "none of the three was kept"
    from tranibor_api.services.handed_files import slug

    assert slug("my quote.pdf") == "my-quote.pdf" and slug("Dévis  n°1 (final).pdf") == "Devis-n1-final-.pdf"
    assert slug("../../etc/pass wd.pdf") == "pass-wd.pdf"
    assert slug("...") == "" and slug("日本語") == ""
    assert len(slug("a" * 300 + ".pdf")) == 128 and slug("a" * 300 + ".pdf").endswith(".pdf")


async def test_a_name_is_not_taken_from_a_steps_output(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.db.models import ArtifactRecord, Project
    from tranibor_api.db.session import session_scope

    item = await _ready(client, project)
    async with session_scope() as session:
        owner = await session.get(Project, project["id"])
        assert owner is not None
        session.add(
            ArtifactRecord(
                org_id=owner.org_id,
                work_item_id=item["id"],
                name="report.pdf",
                version=1,
                type="pdf",
                kind="file",
                media_type="application/pdf",
                digest=artifact_digest(PDF),
                size=len(PDF),
                storage_key="k",
                run_id="run-1",
            )
        )
    answer = await _hand(client, item, ("report.pdf", OTHER_PDF), ("attachments.pdf", PDF))
    assert answer.status_code == 422 and "output of a step" in answer.json()["detail"]


async def test_a_member_without_item_control_gets_403(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.db.models import Membership, Organization, User
    from tranibor_api.db.session import session_scope

    item = await _ready(client, project)
    async with session_scope() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == "northwind"))
        ).scalar_one()
        user = User(email="reader@northwind.dev", display_name="reader")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role="viewer"))
    async with AsyncClient(
        transport=ASGITransport(app=client._transport.app), base_url="http://test"
    ) as other:  # type: ignore[attr-defined]
        await login(other, "reader@northwind.dev")
        answer = await _hand(other, item, ("a.pdf", PDF))
    assert answer.status_code == 403, answer.text
    assert await _rows() == [] and not store.objects


async def test_another_organisations_work_item_is_not_found(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.db.models import Organization, Project, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_api.services import ensure_defaults

    await _ready(client, project)
    async with session_scope() as session:
        org = Organization(slug="contoso", name="Contoso")
        session.add(org)
        await session.flush()
        foreign = Project(
            org_id=org.id,
            slug="intake",
            name="Intake",
            status="active",
            config={"slug": "intake", "org": "contoso"},
        )
        session.add(foreign)
        await session.flush()
        await ensure_defaults(session, foreign)
        row = WorkItem(project_id=foreign.id, tracker_key="CON-1", title="T", state="ready")
        session.add(row)
        await session.flush()
        item = {"id": row.id}
    answer = await _hand(client, item, ("a.pdf", PDF))
    assert answer.status_code == 404, answer.text
    assert await _rows() == [] and not store.objects


async def test_a_terminal_work_item_answers_409(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.db.models import WorkItem
    from tranibor_api.db.session import session_scope

    item = await _ready(client, project)
    async with session_scope() as session:
        row = await session.get(WorkItem, item["id"])
        assert row is not None
        row.state = "abandoned"
    answer = await _hand(client, item, ("a.pdf", PDF))
    assert answer.status_code == 409, answer.text


async def test_a_store_that_fails_leaves_no_row(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.db.models import AuditLog
    from tranibor_api.db.session import session_scope

    item = await _ready(client, project)
    store.fail_next()
    answer = await _hand(client, item, ("a.pdf", PDF), ("b.pdf", OTHER_PDF))
    assert answer.status_code == 502, answer.text
    assert await _rows() == []
    async with session_scope() as session:
        audits = (
            await session.execute(select(AuditLog).where(AuditLog.action == "work_item.file.attached"))
        ).all()
    assert audits == []
    assert (await _hand(client, item, ("a.pdf", PDF))).status_code == 201, "a retry works"


async def test_start_true_on_a_workflow_that_requires_files_is_refused(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.db.models import WorkItem
    from tranibor_api.db.session import session_scope

    await _publish(client, project, {"accept": ["pdf"], "min": 2})
    answer = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Compare the quotes", "start": True}
    )
    assert answer.status_code == 422, answer.text
    detail = answer.json()["detail"]
    assert "start: false" in detail and "/files" in detail and "mark_agent_ready" in detail
    async with session_scope() as session:
        assert (await session.execute(select(WorkItem))).first() is None, "nothing was created"
    await _publish(client, project, {"accept": ["pdf"]})
    assert (
        await client.post(f"/api/v1/projects/{project['id']}/work-items", json={"title": "No minimum"})
    ).status_code == 201


async def test_mark_agent_ready_waits_for_min_files(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.temporal import get_temporal

    item = await _ready(client, project, min=2)
    action = {"action": "mark_agent_ready"}
    assert (await _hand(client, item, ("a.pdf", PDF))).status_code == 201
    refused = await client.post(f"/api/v1/work-items/{item['id']}/actions", json=action)
    assert refused.status_code == 409 and "has 1" in refused.json()["detail"], refused.text
    assert not get_temporal().started, "nothing started"
    assert (await _hand(client, item, ("a.pdf", OTHER_PDF))).status_code == 201
    still = await client.post(f"/api/v1/work-items/{item['id']}/actions", json=action)
    assert still.status_code == 409, "a revision is not a second file"
    assert (await _hand(client, item, ("b.pdf", PDF))).status_code == 201
    started = await client.post(f"/api/v1/work-items/{item['id']}/actions", json=action)
    assert started.status_code == 202, started.text
    assert any(wf.endswith(item["tracker_key"]) for wf in get_temporal().started), "the interpreter started"


async def test_a_file_from_an_application_counts_toward_min(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.db.models import Project, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_api.services.artifacts import put_input_file, record_input_file

    item = await _ready(client, project, min=2)
    assert (await _hand(client, item, ("a.pdf", PDF))).status_code == 201
    async with session_scope() as session:
        work_item = await session.get(WorkItem, item["id"])
        owner = await session.get(Project, project["id"])
        assert work_item is not None and owner is not None
        stored = await put_input_file(
            store, org_id=owner.org_id, work_item_id=item["id"], name="b.pdf", type_="pdf", data=OTHER_PDF
        )
        await record_input_file(session, org_id=owner.org_id, item=work_item, stored=stored)
    started = await client.post(
        f"/api/v1/work-items/{item['id']}/actions", json={"action": "mark_agent_ready"}
    )
    assert started.status_code == 202, started.text


async def test_each_file_leaves_an_audit_entry_without_its_content(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from tranibor_api.db.models import AuditLog
    from tranibor_api.db.session import session_scope

    item = await _ready(client, project)
    await _hand(client, item, ("a.pdf", PDF), ("b.pdf", OTHER_PDF))
    async with session_scope() as session:
        audits = list(
            (
                await session.execute(select(AuditLog).where(AuditLog.action == "work_item.file.attached"))
            ).scalars()
        )
    assert sorted((a.payload["name"], a.payload["digest"], a.payload["size"]) for a in audits) == [
        ("a.pdf", artifact_digest(PDF), len(PDF)),
        ("b.pdf", artifact_digest(OTHER_PDF), len(OTHER_PDF)),
    ]
    assert all(a.target_id == item["id"] and a.target_type == "work_item" for a in audits)
    assert not any("Halden Industrial" in str(a.payload) or "%PDF" in str(a.payload) for a in audits)
