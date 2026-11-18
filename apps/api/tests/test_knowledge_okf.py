"""The Open Knowledge Format at the edge of a knowledge base (S72-01, ADR 0174): a document's OKF header
is read when it comes in, a withdrawn document is neither served nor searched, and what an agent reads
is an OKF bundle in which an imported `verified` claim is never the platform's word."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import yaml
from httpx import AsyncClient

from .test_knowledge_files import _pinned, _run

BASE = "/api/v1/orgs/northwind/knowledge"

IMPORTED = (
    "---\n"
    "type: Table\n"
    "title: Orders\n"
    "description: One row per order.\n"
    "verified:\n"
    "  by: human:someone\n"
    "  at: 2026-07-24T10:00:00Z\n"
    "stale_after: 2027-01-01T00:00:00Z\n"
    "x-team: billing\n"
    "---\n"
    "\n"
    "# Orders\n\n# Schema\n\nThe order id is a UUID.\n"
)
WITHDRAWN = "---\ntype: Table\nstatus: deprecated\n---\n\n# Old orders\n\nThe order id was an integer.\n"


def _header(markdown: str) -> dict[str, Any]:
    from tranibor_api.knowledge import okf

    header, _ = okf.split(markdown)
    assert header is not None, markdown
    return header


# ───────────────────────────── units ─────────────────────────────


def test_a_header_is_read_and_its_body_kept() -> None:
    from tranibor_api.knowledge import okf

    header, body = okf.split(IMPORTED)
    assert header is not None
    assert header["type"] == "Table" and header["x-team"] == "billing"
    assert header["stale_after"] == "2027-01-01T00:00:00Z", "a timestamp stays the text it was written in"
    assert body.startswith("# Orders\n")


@pytest.mark.parametrize(
    "markdown",
    [
        pytest.param("# No header\n\ntext\n", id="none"),
        pytest.param("---\n- a\n- list\n---\n\n# Body\n", id="not-a-mapping"),
        pytest.param("---\nkey: [unclosed\n---\n\n# Body\n", id="not-yaml"),
        pytest.param("---\ntype: x\n\n# never closed\n", id="unclosed"),
        pytest.param("---\nk: " + "a" * (70 * 1024) + "\n---\n\n# Body\n", id="too-large"),
    ],
)
def test_what_is_not_a_header_stays_text(markdown: str) -> None:
    from tranibor_api.knowledge import okf

    assert okf.split(markdown) == (None, markdown)


def test_a_served_document_is_an_okf_concept_and_keeps_what_it_said_of_itself() -> None:
    from tranibor_api.knowledge import okf

    served = okf.served(
        IMPORTED,
        title="ignored: the header names one",
        filename="orders.md",
        source_url="https://git.example/okf/orders.md",
        converted_at=datetime(2026, 11, 16, 9, 30, tzinfo=UTC),
    )
    header = _header(served)
    assert list(header)[:2] == ["type", "title"]
    assert (header["type"], header["title"]) == ("Table", "Orders")
    assert header["resource"] == "https://git.example/okf/orders.md"
    assert header["generated"] == {"by": okf.PRODUCER, "at": "2026-11-16T09:30:00+00:00"}
    assert header["sources"] == [{"resource": "https://git.example/okf/orders.md", "title": "orders.md"}]
    assert header["x-team"] == "billing" and header["description"] == "One row per order.", (
        "unknown keys kept"
    )
    assert header["stale_after"] == "2027-01-01T00:00:00Z"
    assert served.count("\n---\n") == 1 and served.endswith("The order id is a UUID.\n"), "one header"
    assert served == okf.served(
        served, title="x", filename="orders.md", source_url="https://git.example/okf/orders.md",
        converted_at=datetime(2026, 11, 16, 9, 30, tzinfo=UTC),
    ), "serving a served document again changes nothing"  # fmt: skip


def test_an_imported_verified_claim_is_never_served_as_a_verification() -> None:
    """OKF derives a trust tier from `verified` (human-reviewed for `human:`); a header written by
    whoever wrote the file is a claim, not a review the organisation made."""
    from tranibor_api.knowledge import okf

    header = _header(okf.served(IMPORTED, title="Orders", filename="orders.md"))
    assert "verified" not in header
    assert header["source_verified"] == {"by": "human:someone", "at": "2026-07-24T10:00:00Z"}


def test_a_converted_document_without_a_header_gets_one() -> None:
    from tranibor_api.knowledge import okf

    served = okf.served(
        "# Encryption standard\n\nTLS 1.3.\n", title="Encryption standard", filename="enc.docx"
    )
    header = _header(served)
    assert header == {
        "type": okf.DEFAULT_TYPE,
        "title": "Encryption standard",
        "sources": [{"resource": "enc.docx", "title": "enc.docx"}],
    }
    assert served.endswith("# Encryption standard\n\nTLS 1.3.\n")


def test_a_naive_timestamp_from_the_database_is_written_with_its_offset() -> None:
    from tranibor_api.knowledge import okf

    header = _header(
        okf.served("x\n", title="t", filename="f.md", converted_at=datetime(2026, 1, 2, 3, 4, 5))
    )
    assert header["generated"]["at"] == "2026-01-02T03:04:05+00:00"


def test_deprecated_and_the_declared_title() -> None:
    from tranibor_api.knowledge import okf
    from tranibor_api.knowledge.conversion import title_of

    assert okf.deprecated(WITHDRAWN) and not okf.deprecated(IMPORTED) and not okf.deprecated("# x\n")
    assert title_of(IMPORTED, None, "orders.md") == "Orders"
    commented = "---\n# a YAML comment, not a heading\ntype: Note\n---\n\n# The heading\n"
    assert title_of(commented, None, "n.md") == "The heading"


# ───────────────────────────── a run's files ─────────────────────────────


async def test_a_run_is_served_an_okf_bundle_without_the_withdrawn_document(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from tranibor_api.knowledge.conversion import conversions
    from tranibor_contracts import knowledge_digest

    await client.post(BASE, json={"slug": "architecture", "name": "Architecture framework"})
    for name, data in (("orders.md", IMPORTED), ("old-orders.md", WITHDRAWN), ("log.md", "# A log\n")):
        sent = await client.post(f"{BASE}/architecture/documents", files={"file": (name, data.encode())})
        assert sent.status_code == 202, sent.text
    await conversions.drain()
    pinned = await _pinned(project)
    assert len(pinned[0]["documents"]) == 2, "the deprecated document is not pinned"

    token = await _run(project, pinned)
    client.cookies.clear()
    read = await client.get(
        "/api/v1/internal/runs/run-kb/knowledge", headers={"Authorization": f"Bearer {token}"}
    )
    assert read.status_code == 200, read.text
    (base,) = read.json()
    assert base["digest"] == pinned[0]["digest"] == knowledge_digest(base["documents"])
    by_path = {d["path"]: d for d in base["documents"]}
    assert sorted(by_path) == ["log-2.md", "orders.md"], "no document takes a name OKF reserves"
    for document in by_path.values():
        header = yaml.safe_load(document["markdown"].split("---\n")[1])
        assert isinstance(header.get("type"), str) and header["type"], "OKF bundle conformance (§11)"
        assert "verified" not in header
    orders = _header(by_path["orders.md"]["markdown"])
    assert orders["source_verified"]["by"] == "human:someone" and orders["title"] == "Orders"


# ───────────────────────────── the index ─────────────────────────────


async def test_a_withdrawn_document_is_not_searched_and_a_header_is_not_a_passage(
    client: AsyncClient, admin: str
) -> None:
    from tranibor_api.knowledge.conversion import conversions

    created = await client.post(BASE, json={"slug": "catalog", "name": "Catalog", "serving_mode": "index"})
    assert created.status_code == 201, created.text
    for name, data in (("orders.md", IMPORTED), ("old-orders.md", WITHDRAWN)):
        sent = await client.post(f"{BASE}/catalog/documents", files={"file": (name, data.encode())})
        assert sent.status_code == 202, sent.text
    await conversions.drain()
    assert (await client.get(f"{BASE}/catalog")).json()["index"]["documents"] == 1
    found = (await client.post(f"{BASE}/catalog/search", json={"query": "order id integer UUID"})).json()
    assert {hit["filename"] for hit in found["hits"]} == {"orders.md"}
    assert not any("human:someone" in hit["excerpt"] for hit in found["hits"])
    claim = (await client.post(f"{BASE}/catalog/search", json={"query": "human someone verified"})).json()
    assert not any("human:someone" in hit["excerpt"] for hit in claim["hits"])


# ───────────────────────────── export and the console's field (S72-03) ─────────────────────────────


def test_a_lifecycle_says_status_and_staleness() -> None:
    from tranibor_api.knowledge import okf

    now = datetime(2026, 11, 16, tzinfo=UTC)
    assert okf.lifecycle("# no header\n", now) is None
    assert okf.lifecycle(IMPORTED, now) == {
        "type": "Table",
        "status": "stable",
        "stale_after": "2027-01-01T00:00:00Z",
        "stale": False,
    }
    assert okf.lifecycle(IMPORTED, datetime(2027, 1, 1, tzinfo=UTC))["stale"] is True  # type: ignore[index]
    assert okf.lifecycle(WITHDRAWN, now)["status"] == "deprecated"  # type: ignore[index]
    odd = "---\ntype: x\nstatus: retired\nstale_after: 2020-01-01\n---\n"
    assert okf.lifecycle(odd, now) == {
        "type": "x",
        "status": "stable",
        "stale_after": "2020-01-01",
        "stale": False,
    }, "an unknown status is stable; an instant without an offset is never past"


async def test_a_listing_shows_each_document_s_okf_lifecycle(client: AsyncClient, admin: str) -> None:
    from tranibor_api.knowledge.conversion import conversions

    await client.post(BASE, json={"slug": "catalog", "name": "Catalog"})
    for name, data in (("orders.md", IMPORTED), ("old-orders.md", WITHDRAWN), ("plain.md", "# Plain\n")):
        await client.post(f"{BASE}/catalog/documents", files={"file": (name, data.encode())})
    await conversions.drain()
    base = (await client.get(f"{BASE}/catalog")).json()
    by_name = {d["filename"]: d for d in base["documents"]}
    assert by_name["orders.md"]["okf"] == {
        "type": "Table",
        "status": "stable",
        "stale_after": "2027-01-01T00:00:00Z",
        "stale": False,
    }
    assert by_name["old-orders.md"]["okf"]["status"] == "deprecated"
    assert by_name["plain.md"]["okf"] is None
    one = (await client.get(f"{BASE}/catalog/documents/{by_name['orders.md']['id']}")).json()
    assert one["okf"]["type"] == "Table" and one["markdown"].startswith("---\ntype: Table\n")


async def test_a_base_exports_as_an_okf_bundle_its_reader_may_take_and_it_is_audited(
    client: AsyncClient, admin: str
) -> None:
    import io
    import zipfile

    from tranibor_api.knowledge import okf
    from tranibor_api.knowledge.conversion import conversions

    await client.post(BASE, json={"slug": "catalog", "name": "Catalog"})
    for name, data in (("orders.md", IMPORTED), ("old-orders.md", WITHDRAWN)):
        await client.post(f"{BASE}/catalog/documents", files={"file": (name, data.encode())})
    await conversions.drain()
    exported = await client.get(f"{BASE}/catalog/export")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/zip"
    assert exported.headers["content-disposition"] == 'attachment; filename="catalog.okf.zip"'
    archive = zipfile.ZipFile(io.BytesIO(exported.content))
    assert sorted(archive.namelist()) == ["catalog/index.md", "catalog/old-orders.md", "catalog/orders.md"]
    index = archive.read("catalog/index.md").decode()
    assert index.startswith(f'---\nokf_version: "{okf.OKF_VERSION}"\n---\n\n# Catalog\n')
    assert "[Orders](/orders.md)" in index
    for name in ("catalog/orders.md", "catalog/old-orders.md"):
        header = _header(archive.read(name).decode())
        assert header["type"] == "Table" and "verified" not in header, "OKF §11, and no claim as a review"
    assert _header(archive.read("catalog/old-orders.md").decode())["status"] == "deprecated", (
        "the export is the base, its withdrawn documents with their status"
    )
    assert (await client.get(f"{BASE}/catalog/export")).content == exported.content, "deterministic"
    audit = (await client.get("/api/v1/audit", params={"target_type": "knowledge_base"})).json()["items"]
    exports = [e for e in audit if e["action"] == "knowledge.export"]
    assert len(exports) == 2, "each export is recorded"
    assert (await client.get(f"{BASE}/nothing-here/export")).status_code == 404
