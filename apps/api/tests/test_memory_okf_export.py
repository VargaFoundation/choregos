"""A memory's accepted facts exported as an Open Knowledge Format bundle (S72-04, ADR 0174 point 7).

The export is one memory's active facts, and only that memory's; `verified` is written only where a
person accepted the fact through the API, as the audit log says; a backend that cannot list one
memory apart from the others (Ecphoria) is refused, never approximated.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import yaml
from httpx import AsyncClient
from tranibor_core.domain import Fact, Memory, Provenance

from .agent_scopes_kit import ORG, add_member, as_user, make_project

pytestmark = pytest.mark.usefixtures("real_adapters")


@pytest.fixture
def real_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_FAKES", "0")


def _memory() -> Any:
    from tranibor_adapters import build

    return build("memory", "lexical", {"org": "northwind"})


def _bundle(content: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def _header(markdown: str) -> dict[str, Any]:
    assert markdown.startswith("---\n"), markdown
    return yaml.safe_load(markdown.split("---\n", 2)[1])


async def _project(client: AsyncClient) -> str:
    project = await make_project(client, "billing-api")
    pid = str(project["id"])
    put = await client.put(f"/api/v1/projects/{pid}/connectors/memory", json={"type": "lexical"})
    assert put.status_code in {200, 201}, put.text
    made = await client.post(
        f"/api/v1/projects/{pid}/memories", json={"slug": "decisions", "display_name": "D"}
    )
    assert made.status_code == 201, made.text
    return pid


async def test_a_project_memory_exports_its_active_facts_and_who_accepted_them(
    client: AsyncClient, admin: str
) -> None:
    pid = await _project(client)
    me = (await client.get("/api/v1/me")).json()["id"]
    memory = _memory()
    await memory.write_fact("billing-api", Fact(kind="decision", subject="vat", content="VAT is 20 %."))
    await memory.write_fact(
        "billing-api#agent:architect", Fact(kind="decision", subject="own", content="in the own memory")
    )
    await memory.write_fact(
        "billing-api#memory:decisions", Fact(kind="decision", subject="named", content="in the named memory")
    )
    accepted = await memory.propose_fact(
        "billing-api",
        Fact(kind="run_lesson", subject="fx", content="Convert at the daily rate."),
        Provenance(source="agent", run_id="r1"),
    )
    await memory.propose_fact(
        "billing-api",
        Fact(kind="run_lesson", subject="waiting", content="Still pending."),
        Provenance(source="agent"),
    )
    auto = await memory.propose_fact(
        "billing-api",
        Fact(kind="run_lesson", subject="auto", content="Taken by the auto-accept rule."),
        Provenance(source="agent", run_id="r2"),
    )
    url = f"/api/v1/projects/{pid}/memory/pending"
    # A decision made where the fact is not finds nothing, and is no verification of it: the fact the
    # auto-accept rule then takes was accepted by no person.
    wrong = await client.post(url, json={"id": auto, "action": "accept"}, params={"memory": "decisions"})
    assert wrong.json() == {"status": "not_found"}
    assert await memory.accept_pending("billing-api", auto)
    assert (await client.post(url, json={"id": accepted, "action": "accept"})).json() == {
        "status": "accepted"
    }

    response = await client.get(f"/api/v1/projects/{pid}/memory/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="billing-api-shared.okf.zip"' in response.headers["content-disposition"]
    files = _bundle(response.content)
    assert files["billing-api-shared/index.md"].startswith('---\nokf_version: "0.2"\n---')
    facts = {name: _header(text) for name, text in files.items() if name != "billing-api-shared/index.md"}
    assert sorted(h["title"] for h in facts.values()) == ["auto", "fx", "vat"], (
        "this memory's active facts only"
    )
    by_title = {h["title"]: h for h in facts.values()}
    assert by_title["fx"]["type"] == "run_lesson"
    assert by_title["fx"]["verified"][0]["by"] == f"human:{me}"
    assert by_title["fx"]["sources"] == [{"resource": "run:r1", "title": "the run that proposed it"}]
    assert "verified" not in by_title["vat"], "a fact nobody accepted carries no verification"
    assert "verified" not in by_title["auto"], "a decision that found nothing verifies nothing"
    assert any(text.endswith("Convert at the daily rate.\n") for text in files.values())

    own = await client.get(
        f"/api/v1/projects/{pid}/memory/export", params={"memory": "own", "agent": "architect"}
    )
    assert [_header(t)["title"] for n, t in _bundle(own.content).items() if not n.endswith("/index.md")] == [
        "own"
    ]
    assert (await client.get(f"/api/v1/projects/{pid}/memory/export")).content == response.content, (
        "the same memory exports the same bytes"
    )


async def test_a_global_memory_exports_for_whoever_may_search_it(client: AsyncClient, admin: str) -> None:
    await _project(client)
    await client.post(f"{ORG}/memories", json={"slug": "lessons", "display_name": "Lessons"})
    declared = await client.post(
        f"{ORG}/connectors", json={"name": "memory-org", "type": "lexical", "kind": "memory"}
    )
    assert declared.status_code == 201, declared.text
    ns = "org:northwind#memory:lessons"
    memory = _memory()
    proposed = await memory.propose_fact(
        ns,
        Fact(kind="decision", subject="tls", content="Pin TLS 1.2."),
        Provenance(source="agent", run_id="r9"),
    )
    decided = await client.post(f"{ORG}/memories/lessons/pending", json={"id": proposed, "action": "accept"})
    assert decided.json() == {"status": "accepted"}

    response = await client.get(f"{ORG}/memories/lessons/export")
    assert response.status_code == 200, response.text
    files = _bundle(response.content)
    assert "# Lessons" in files["lessons/index.md"]
    [fact] = [_header(t) for n, t in files.items() if n != "lessons/index.md"]
    assert fact["title"] == "tls" and fact["verified"][0]["by"].startswith("human:")

    await add_member(client, "dev@northwind.dev", "developer")
    await as_user(client, "dev@northwind.dev")
    assert (await client.get(f"{ORG}/memories/lessons/export")).status_code == 200
    assert (await client.get(f"{ORG}/memories/nothing/export")).status_code == 404


def test_a_backend_that_cannot_list_one_memory_is_refused() -> None:
    """Ecphoria ignores the namespace header: a listing would mix every memory of its tenant."""
    from tranibor_adapters.memory.ecphoria import EcphoriaMemory
    from tranibor_api.routers.memory import refuse_unlisted

    with pytest.raises(Exception) as refused:
        refuse_unlisted(EcphoriaMemory("http://ecphoria.invalid"))
    assert "cannot list one memory's facts" in str(refused.value.detail)  # type: ignore[attr-defined]


def test_a_fact_whose_validity_ended_is_deprecated() -> None:
    from tranibor_api.services.memory_export import fact_markdown

    now = datetime(2026, 11, 17, tzinfo=UTC)
    ended = Memory(
        id="m1",
        kind="decision",
        subject="old",
        content="Superseded.",
        valid_from=now - timedelta(days=30),
        valid_to=now - timedelta(days=1),
    )
    header = _header(fact_markdown(ended, None, now))
    assert header["status"] == "deprecated"
    assert header["valid_to"] == "2026-11-16T00:00:00+00:00"
    assert "verified" not in header
    current = _header(
        fact_markdown(ended.model_copy(update={"valid_to": now + timedelta(days=1)}), None, now)
    )
    assert "status" not in current
