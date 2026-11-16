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

BASE = "/api/v1/orgs/varga/knowledge"

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
    from choregos_api.knowledge import okf

    header, _ = okf.split(markdown)
    assert header is not None, markdown
    return header


# ───────────────────────────── units ─────────────────────────────


def test_a_header_is_read_and_its_body_kept() -> None:
    from choregos_api.knowledge import okf

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
    from choregos_api.knowledge import okf

    assert okf.split(markdown) == (None, markdown)


def test_a_served_document_is_an_okf_concept_and_keeps_what_it_said_of_itself() -> None:
    from choregos_api.knowledge import okf

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
    from choregos_api.knowledge import okf

    header = _header(okf.served(IMPORTED, title="Orders", filename="orders.md"))
    assert "verified" not in header
    assert header["source_verified"] == {"by": "human:someone", "at": "2026-07-24T10:00:00Z"}


def test_a_converted_document_without_a_header_gets_one() -> None:
    from choregos_api.knowledge import okf

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
    from choregos_api.knowledge import okf

    header = _header(
        okf.served("x\n", title="t", filename="f.md", converted_at=datetime(2026, 1, 2, 3, 4, 5))
    )
    assert header["generated"]["at"] == "2026-01-02T03:04:05+00:00"


def test_deprecated_and_the_declared_title() -> None:
    from choregos_api.knowledge import okf
    from choregos_api.knowledge.conversion import title_of

    assert okf.deprecated(WITHDRAWN) and not okf.deprecated(IMPORTED) and not okf.deprecated("# x\n")
    assert title_of(IMPORTED, None, "orders.md") == "Orders"
    commented = "---\n# a YAML comment, not a heading\ntype: Note\n---\n\n# The heading\n"
    assert title_of(commented, None, "n.md") == "The heading"


# ───────────────────────────── a run's files ─────────────────────────────


async def test_a_run_is_served_an_okf_bundle_without_the_withdrawn_document(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.knowledge.conversion import conversions
    from choregos_contracts import knowledge_digest

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
    from choregos_api.knowledge.conversion import conversions

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
