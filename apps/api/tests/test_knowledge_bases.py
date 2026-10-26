"""Knowledge bases (S33-01, ADR 0051): a curated corpus, uploaded, converted to markdown in the background."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from . import knowledge_fixtures as fx
from .conftest import login

BASE = "/api/v1/orgs/varga/knowledge"


async def _base(client: AsyncClient, slug: str = "architecture", **fields: Any) -> dict[str, Any]:
    created = await client.post(BASE, json={"slug": slug, "name": slug.title(), **fields})
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _upload(client: AsyncClient, name: str, data: bytes, slug: str = "architecture") -> Any:
    return await client.post(f"{BASE}/{slug}/documents", files={"file": (name, data)})


async def _converted(
    client: AsyncClient, name: str, data: bytes, slug: str = "architecture"
) -> dict[str, Any]:
    from choregos_api.knowledge.conversion import conversions

    sent = await _upload(client, name, data, slug)
    assert sent.status_code == 202, sent.text
    await conversions.drain()
    read = await client.get(f"{BASE}/{slug}/documents/{sent.json()['id']}")
    assert read.status_code == 200, read.text
    return dict(read.json())


async def test_a_pptx_becomes_markdown_with_its_slide_titles(client: AsyncClient, admin: str) -> None:
    """The acceptance of S33-01: one `#` title per slide, and the table of a slide kept as a table."""
    await _base(client)
    deck = fx.pptx(
        [("Network zoning principles", "Every zone has a firewall"), ("Encryption", "TLS 1.3 everywhere")],
        table=[["zone", "owner"], ["dmz", "netops"]],
    )
    document = await _converted(client, "principles.pptx", deck)
    assert document["status"] == "ready", document["error"]
    markdown = document["markdown"]
    assert "# Network zoning principles" in markdown
    assert "# Encryption" in markdown
    assert "| zone | owner |" in markdown and "| dmz | netops |" in markdown
    assert document["title"] == "Network zoning principles"
    assert document["mime"].endswith("presentationml.presentation")


async def test_a_word_document_keeps_its_headings_and_tables(client: AsyncClient, admin: str) -> None:
    await _base(client)
    word = fx.docx(
        [(1, "Architecture principles", ""), (2, "Encryption", "TLS 1.3 everywhere.")],
        table=[["control", "owner"], ["backup", "ops"]],
    )
    markdown = (await _converted(client, "principles.docx", word))["markdown"]
    assert "# Architecture principles" in markdown
    assert "## Encryption" in markdown
    assert "| backup | ops |" in markdown


async def test_a_spreadsheet_gives_one_table_per_sheet(client: AsyncClient, admin: str) -> None:
    await _base(client)
    book = fx.xlsx({"Costs": [["item", "eur"], ["vm", 3]], "Owners": [["team", "lead"], ["netops", "Ada"]]})
    markdown = (await _converted(client, "costs.xlsx", book))["markdown"]
    assert "## Costs" in markdown and "| vm | 3 |" in markdown
    assert "## Owners" in markdown and "| netops | Ada |" in markdown


async def test_a_spreadsheet_cut_at_its_row_cap_says_so_in_the_document(
    client: AsyncClient, admin: str
) -> None:
    """S42-09e: the document list carries what the agents' markdown says; a reconversion starts clean."""
    await _base(client)
    rows: list[list[object]] = [["id"]] + [[i] for i in range(2100)]
    document = await _converted(client, "big.xlsx", fx.xlsx({"Big": rows, "Small": [["a"], [1]]}))
    expected = ['Sheet "Big": 101 more rows not shown (the first 2000 are kept)']
    assert document["status"] == "ready" and document["warnings"] == expected
    listed = (await client.get(f"{BASE}/architecture")).json()["documents"]
    assert [d["warnings"] for d in listed] == [expected]
    small = await _converted(client, "small.xlsx", fx.xlsx({"Small": [["a"], [1]]}))
    assert small["warnings"] == []


@pytest.mark.parametrize(
    ("name", "data", "expected", "title"),
    [
        ("page.html", b"<html><head><title>Runbook</title></head><body><h1>Restart</h1><p>step</p>"
         b"<script>alert(1)</script></body></html>", "# Restart", "Runbook"),
        ("notes.md", b"# Notes\n\n## Rules\n\nkeep it small\n", "## Rules", "Notes"),
        ("readme.txt", b"plain text guidance\n", "plain text guidance", "readme"),
        ("policy.pdf", fx.pdf(["Retention policy", "Logs are kept 90 days"]), "Logs are kept 90 days",
         "policy"),
    ],
)  # fmt: skip
async def test_html_markdown_text_and_pdf_are_converted(
    client: AsyncClient, admin: str, name: str, data: bytes, expected: str, title: str
) -> None:
    await _base(client)
    document = await _converted(client, name, data)
    assert document["status"] == "ready", document["error"]
    assert expected in document["markdown"]
    assert "alert(1)" not in document["markdown"]
    assert document["title"] == title


async def test_the_upload_answers_pending_and_converts_outside_the_request(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request stores and schedules; it never converts. Without the queue, nothing converts."""
    from choregos_api.knowledge import conversion

    scheduled: list[tuple[str, str]] = []
    monkeypatch.setattr(conversion.conversions, "schedule", lambda org, doc: scheduled.append((org, doc)))
    await _base(client)
    sent = await _upload(client, "notes.md", b"# Notes\n")
    assert sent.status_code == 202, sent.text
    assert sent.json()["status"] == "pending"
    assert scheduled == [("varga", sent.json()["id"])]
    assert (await client.get(f"{BASE}/architecture/documents/{sent.json()['id']}")).json()["markdown"] is None

    assert await conversion.convert_document("varga", sent.json()["id"]) == "ready"
    assert await conversion.convert_document("varga", sent.json()["id"]) == "", "claimed once, not twice"
    read = (await client.get(f"{BASE}/architecture/documents/{sent.json()['id']}")).json()
    assert read["status"] == "ready" and read["markdown"].startswith("# Notes")


async def test_the_same_bytes_twice_are_one_document(client: AsyncClient, admin: str) -> None:
    from choregos_api.knowledge.conversion import conversions

    await _base(client)
    first = await _upload(client, "notes.md", b"# Notes\n")
    again = await _upload(client, "copy.md", b"# Notes\n")
    await conversions.drain()
    assert (first.status_code, again.status_code) == (202, 200)
    assert again.json()["id"] == first.json()["id"]
    base = (await client.get(f"{BASE}/architecture")).json()
    assert base["document_count"] == 1
    assert base["documents_by_status"] == {"ready": 1}


@pytest.mark.parametrize(
    ("name", "data", "reason"),
    [
        ("tool.exe", b"MZ\x90\x00", "unsupported format"),
        ("deck.pptx", b"%PDF-1.4 not a deck", "not an Office document"),
        ("doc.pdf", b"PK\x03\x04 not a pdf", "does not start like a PDF"),
        ("notes.md", b"\x00\x01\x02binary", "binary, not text"),
        ("empty.txt", b"", "is empty"),
        ("archive.docx", fx.broken_office()[:30], "damaged archive"),
    ],
)
async def test_a_file_that_is_not_what_it_claims_is_refused(
    client: AsyncClient, admin: str, name: str, data: bytes, reason: str
) -> None:
    await _base(client)
    refused = await _upload(client, name, data)
    assert refused.status_code == 422, refused.text
    assert reason in refused.json()["detail"]


async def test_an_office_archive_that_would_inflate_too_much_is_refused(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge import formats

    monkeypatch.setattr(formats, "MAX_INFLATED", 1024)
    await _base(client)
    refused = await _upload(client, "big.docx", fx.docx([(1, "Big", "x" * 5000)]))
    assert refused.status_code == 422
    assert "would inflate" in refused.json()["detail"]


async def test_a_file_over_the_cap_is_refused(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_KNOWLEDGE_MAX_UPLOAD_MIB", "1")
    reset_settings_cache()
    await _base(client)
    refused = await _upload(client, "huge.txt", b"a" * (1024 * 1024 + 1))
    assert refused.status_code == 413, refused.text
    assert (await client.get(f"{BASE}/architecture")).json()["document_count"] == 0


async def test_a_conversion_that_fails_says_why_and_can_be_retried(client: AsyncClient, admin: str) -> None:
    from choregos_api.knowledge.conversion import conversions

    await _base(client)
    document = await _converted(client, "broken.docx", fx.broken_office())
    assert document["status"] == "failed"
    assert document["error"], "a failure carries its reason"
    assert document["markdown"] is None
    again = await client.post(f"{BASE}/architecture/documents/{document['id']}/convert")
    assert again.status_code == 202 and again.json()["status"] == "pending"
    await conversions.drain()
    assert (await client.get(f"{BASE}/architecture/documents/{document['id']}")).json()["status"] == "failed"


async def test_a_conversion_past_its_timeout_is_stopped(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child process is killed: a hostile or huge document cannot hold a worker forever."""
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_KNOWLEDGE_CONVERSION_TIMEOUT_S", "0.05")
    reset_settings_cache()
    await _base(client)
    document = await _converted(client, "notes.md", b"# Notes\n")
    assert document["status"] == "failed"
    assert "longer than 0.05 s" in document["error"]


def test_the_converter_inherits_none_of_the_api_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.knowledge.conversion import child_environment

    monkeypatch.setenv("CHOREGOS_DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("CHOREGOS_GATEWAY_MASTER_KEY", "sk-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = child_environment()
    assert env["PATH"] == "/usr/bin"
    assert not [key for key in env if key.startswith("CHOREGOS_")]


async def test_a_stopped_replica_leaves_nothing_behind(client: AsyncClient, admin: str) -> None:
    """Pending documents, and `converting` ones gone stale, are converted at the next start-up."""
    from datetime import timedelta

    from choregos_api.db.models import KnowledgeDocument
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.conversion import conversions
    from choregos_core import utcnow
    from sqlalchemy import update

    base = await _base(client)
    ids = []
    for name in ("a.md", "b.md"):
        sent = await client.post(
            f"{BASE}/architecture/documents", files={"file": (name, f"# {name}\n".encode())}
        )
        ids.append(sent.json()["id"])
    await conversions.stop()  # the replica dies before converting anything
    async with session_scope() as session:
        await session.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == ids[0])
            .values(status="converting", updated_at=utcnow() - timedelta(hours=1))
        )
        await session.execute(
            update(KnowledgeDocument).where(KnowledgeDocument.id == ids[1]).values(status="pending")
        )
    assert await conversions.recover() == 2
    await conversions.drain()
    read = (await client.get(f"{BASE}/{base['slug']}")).json()
    assert read["documents_by_status"] == {"ready": 2}


async def test_only_an_org_admin_curates(client: AsyncClient, admin: str) -> None:
    await _base(client)
    await client.post("/api/v1/orgs/varga/members", json={"email": "dev@varga.dev", "role": "developer"})
    await login(client, "dev@varga.dev")
    assert (await client.post(BASE, json={"slug": "mine", "name": "Mine"})).status_code == 403
    assert (await _upload(client, "notes.md", b"# Notes\n")).status_code == 403
    assert (await client.patch(f"{BASE}/architecture", json={"name": "x"})).status_code == 403
    listed = await client.get(BASE)
    assert [b["slug"] for b in listed.json()] == ["architecture"], "an org-wide base is read by members"


async def test_a_base_outside_the_callers_scope_does_not_exist_for_it(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    await _base(client, "billing-only", scope="projects", projects=["billing-api"])
    await _base(client, "auditors", scope="groups", groups=["auditors"])
    await _base(client, "everyone")
    await login(client, "dev@varga.dev")
    assert [b["slug"] for b in (await client.get(BASE)).json()] == ["billing-only", "everyone"]
    assert (await client.get(f"{BASE}/auditors")).status_code == 404, "a session carries no IdP group yet"
    await login(client, "admin@varga.dev")
    assert {b["slug"] for b in (await client.get(BASE)).json()} == {"auditors", "billing-only", "everyone"}


def test_scope_decides_who_reads_and_which_runs_are_served() -> None:
    """A project-only member reads the bases of ITS projects; a run is never served a group base."""
    from types import SimpleNamespace

    from choregos_api.knowledge.access import served_to_project, visible_to
    from choregos_api.rbac import Principal
    from choregos_contracts import Role

    projects = SimpleNamespace(scope="projects", projects=["billing-api"], groups=[], status="active")
    groups = SimpleNamespace(scope="groups", projects=[], groups=["auditors"], status="active")
    org = SimpleNamespace(scope="org", projects=[], groups=[], status="active")
    web_only = Principal(user_id="u", email="w@x", project_roles={"varga/web": Role.DEVELOPER})
    billing_only = Principal(user_id="u", email="b@x", project_roles={"varga/billing-api": Role.VIEWER})
    auditor = Principal(user_id="u", email="a@x", org_roles={"varga": Role.VIEWER}, groups=["auditors"])
    other_org = Principal(user_id="u", email="o@x", org_roles={"other": Role.ORG_ADMIN})

    assert visible_to(billing_only, "varga", projects) and not visible_to(web_only, "varga", projects)
    assert not visible_to(billing_only, "varga", org), "org-wide reading needs an org role"
    assert visible_to(auditor, "varga", groups) and visible_to(auditor, "varga", org)
    assert not visible_to(other_org, "varga", org)
    assert served_to_project(org, "web") and served_to_project(projects, "billing-api")
    assert not served_to_project(projects, "web")
    assert not served_to_project(groups, "billing-api"), "a run carries no group"
    org.status = "archived"
    assert not served_to_project(org, "web"), "an archived base serves nothing"


async def test_a_scope_names_its_members_and_real_projects(client: AsyncClient, admin: str) -> None:
    empty = await client.post(BASE, json={"slug": "x-1", "name": "X", "scope": "projects"})
    assert empty.status_code == 422
    unknown = await client.post(
        BASE, json={"slug": "x-2", "name": "X", "scope": "projects", "projects": ["nope"]}
    )
    assert unknown.status_code == 422 and "nope" in unknown.json()["detail"]
    await _base(client)
    assert (await client.post(BASE, json={"slug": "architecture", "name": "again"})).status_code == 409
    patched = await client.patch(f"{BASE}/architecture", json={"serving_mode": "index", "status": "archived"})
    assert patched.status_code == 200
    assert (patched.json()["serving_mode"], patched.json()["status"]) == ("index", "archived")
    assert (await _upload(client, "notes.md", b"# Notes\n")).status_code == 409, (
        "an archived base takes nothing"
    )


async def test_a_deleted_document_is_gone(client: AsyncClient, admin: str) -> None:
    await _base(client)
    document = await _converted(client, "notes.md", b"# Notes\n")
    assert (await client.delete(f"{BASE}/architecture/documents/{document['id']}")).status_code == 204
    assert (await client.get(f"{BASE}/architecture/documents/{document['id']}")).status_code == 404
