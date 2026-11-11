"""The documentation over the MCP door (S62-10, ADR 0141): `docs_search` and `docs_read`.

The documentation is the Markdown of `docs/site/` that the API image carries; here, a fixture tree
under `CHOREGOS_DOCS_DIR`. The tools are announced to every token that opens the door, read only,
audited and rate limited like the door's other reads, and read nothing outside that directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient, Response

from .conftest import login

ADMIN = "admin@varga.dev"
BASE = "https://docs.example.test/choregos"

WORKFLOWS = """# Workflows

A workflow moves a work item through states. The kumquat is this page's own word.
Triggers can start one; triggers are described elsewhere, and triggers are not this page:
triggers, triggers, triggers, triggers and triggers again.

## Gates

A gate is checked before a transition.

### Gate kinds

Tests, reviews, budgets.

## Transitions

Who moves a work item: an agent, a person, the system.

```yaml
# not a heading
```
"""

TRIGGERS = (
    """# Triggers

A trigger opens a work item from an outside event.

"""
    + " ".join(f"filler{n}" for n in range(60))
    + "\n"
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(root: Path) -> Path:
    site = root / "site"
    _write(site / "index.md", "---\ntitle: Choregos\n---\n\nThe overview of governed agents.\n")
    _write(site / "01-use" / "workflows.md", f"---\ntitle: Workflows\nsidebar_position: 2\n---\n{WORKFLOWS}")
    _write(site / "03-integrate" / "triggers.md", TRIGGERS)
    _write(site / "concepts" / "index.md", "# Concepts\n\nAgents and people, under gates.\n")
    _write(site / "01-use" / "_partial.md", "# Partial\n\nzanzibar, never a page of its own.\n")
    _write(
        site / "01-use" / "big.md", "# Big\n\n" + "".join(f"line {n} of a long page\n" for n in range(4000))
    )
    _write(root / "secret.md", "# Secret\n\nquetzalcoatl, outside the documentation.\n")
    os.symlink(root / "secret.md", site / "01-use" / "linked.md")
    return site


@pytest.fixture
def docs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: Any) -> Path:
    from choregos_api.config import reset_settings_cache

    site = _tree(tmp_path / "documentation")
    monkeypatch.setenv("CHOREGOS_DOCS_DIR", str(site))
    monkeypatch.setenv("CHOREGOS_DOCS_URL", BASE)
    reset_settings_cache()
    return site


async def _token(client: AsyncClient, *scopes: str, project: str | None = None) -> str:
    body: dict[str, Any] = {"name": "claude-code", "scopes": list(scopes)}
    if project:
        body["project"] = project
    created = await client.post("/api/v1/me/tokens", json=body)
    assert created.status_code == 201, created.text
    token = str(created.json()["token"])
    client.cookies.clear()
    return token


async def _rpc(
    client: AsyncClient, token: str, method: str, params: dict[str, Any] | None = None, path: str = "/mcp"
) -> Response:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        message["params"] = params
    return await client.post(path, json=message, headers={"Authorization": f"Bearer {token}"})


async def _tool(client: AsyncClient, token: str, name: str, **arguments: Any) -> dict[str, Any]:
    response = await _rpc(client, token, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    return dict(response.json()["result"])


# ───────────────────────────── announced ─────────────────────────────


async def test_both_tools_are_announced_read_only_on_every_door(
    client: AsyncClient, project: dict[str, Any], docs_dir: Path
) -> None:
    token = await _token(client, "mcp:read")
    for path in ("/mcp", "/mcp/projects/varga:billing-api"):
        tools = (await _rpc(client, token, "tools/list", path=path)).json()["result"]["tools"]
        found = {o["name"]: o for o in tools}
        assert {"docs_search", "docs_read"} <= set(found), path
        assert found["docs_search"]["annotations"]["readOnlyHint"] is True
        assert found["docs_read"]["annotations"]["readOnlyHint"] is True
        assert "project" not in found["docs_read"]["inputSchema"]["properties"]
    instructions = (await _rpc(client, token, "initialize", {})).json()["result"]["instructions"]
    assert "docs_search" in instructions and "docs_read" in instructions


async def test_a_token_bound_to_a_project_reads_the_documentation_too(
    client: AsyncClient, project: dict[str, Any], docs_dir: Path
) -> None:
    token = await _token(client, "mcp:read", project="varga:billing-api")
    result = await _tool(client, token, "docs_search", query="kumquat")
    assert result["isError"] is False
    assert [p["slug"] for p in result["structuredContent"]["pages"]] == ["use/workflows"]


# ───────────────────────────── docs_search ─────────────────────────────


async def test_search_finds_a_page_by_a_word_of_its_body(
    client: AsyncClient, admin: str, docs_dir: Path
) -> None:
    token = await _token(client, "mcp:read")
    result = await _tool(client, token, "docs_search", query="kumquat")
    assert result["isError"] is False, result
    (page,) = result["structuredContent"]["pages"]
    assert page["slug"] == "use/workflows", "the number prefix is stripped, as on the public site"
    assert page["title"] == "Workflows"
    assert page["url"] == f"{BASE}/use/workflows"
    assert "kumquat" in page["snippet"]
    assert len(page["snippet"]) < 300
    assert f"{BASE}/use/workflows" in result["content"][0]["text"]


async def test_a_title_match_ranks_first(client: AsyncClient, admin: str, docs_dir: Path) -> None:
    """`workflows` says `triggers` eight times; the page titled Triggers still comes first, by its title."""
    token = await _token(client, "mcp:read")
    pages = (await _tool(client, token, "docs_search", query="triggers"))["structuredContent"]["pages"]
    assert [p["slug"] for p in pages][:2] == ["integrate/triggers", "use/workflows"]
    assert pages[0]["score"] > pages[1]["score"]


async def test_an_index_page_has_the_folders_url(client: AsyncClient, admin: str, docs_dir: Path) -> None:
    token = await _token(client, "mcp:read")
    pages = (await _tool(client, token, "docs_search", query="concepts"))["structuredContent"]["pages"]
    assert pages[0]["slug"] == "concepts"
    assert pages[0]["url"] == f"{BASE}/concepts/"
    overview = (await _tool(client, token, "docs_search", query="overview governed"))["structuredContent"]
    assert overview["pages"][0] == {
        **overview["pages"][0],
        "slug": "",
        "url": f"{BASE}/",
        "title": "Choregos",
    }


async def test_no_match_is_an_empty_answer_and_an_empty_query_is_refused(
    client: AsyncClient, admin: str, docs_dir: Path
) -> None:
    token = await _token(client, "mcp:read")
    nothing = await _tool(client, token, "docs_search", query="xylophone")
    assert nothing["isError"] is False and nothing["structuredContent"]["pages"] == []
    empty = await _tool(client, token, "docs_search", query="  ")
    assert empty["isError"] is True


# ───────────────────────────── docs_read ─────────────────────────────


async def test_read_returns_the_pages_markdown_without_its_front_matter(
    client: AsyncClient, admin: str, docs_dir: Path
) -> None:
    token = await _token(client, "mcp:read")
    result = await _tool(client, token, "docs_read", slug="use/workflows")
    assert result["isError"] is False, result
    structured = result["structuredContent"]
    assert structured["markdown"] == WORKFLOWS
    assert result["content"][0]["text"] == WORKFLOWS
    assert structured["url"] == f"{BASE}/use/workflows"
    assert structured["next_offset"] is None
    assert structured["sections"] == ["workflows", "gates", "gate-kinds", "transitions"]
    for alias in (f"{BASE}/use/workflows", "/choregos/use/workflows/", "use/workflows.md"):
        again = await _tool(client, token, "docs_read", slug=alias)
        assert again["structuredContent"]["markdown"] == WORKFLOWS, alias


async def test_read_one_section_by_its_anchor(client: AsyncClient, admin: str, docs_dir: Path) -> None:
    token = await _token(client, "mcp:read")
    expected = WORKFLOWS[WORKFLOWS.index("## Gates") : WORKFLOWS.index("## Transitions")]
    result = await _tool(client, token, "docs_read", slug="use/workflows", section="gates")
    assert result["structuredContent"]["markdown"] == expected
    assert "### Gate kinds" in expected, "a subsection belongs to its section"
    assert result["structuredContent"]["url"] == f"{BASE}/use/workflows#gates"
    by_fragment = await _tool(client, token, "docs_read", slug="use/workflows#gate-kinds")
    assert by_fragment["structuredContent"]["markdown"] == "### Gate kinds\n\nTests, reviews, budgets.\n\n"
    unknown = await _tool(client, token, "docs_read", slug="use/workflows", section="nowhere")
    assert unknown["isError"] is True and "transitions" in unknown["content"][0]["text"]


async def test_a_long_page_comes_in_bounded_parts(client: AsyncClient, admin: str, docs_dir: Path) -> None:
    from choregos_api.mcp.docs import READ_CHUNK

    token = await _token(client, "mcp:read")
    whole = (docs_dir / "01-use" / "big.md").read_text(encoding="utf-8")
    assert len(whole) > 2 * READ_CHUNK
    parts, offset = [], 0
    while True:
        result = await _tool(client, token, "docs_read", slug="use/big", offset=offset)
        structured = result["structuredContent"]
        assert len(structured["markdown"]) <= READ_CHUNK
        parts.append(structured["markdown"])
        if structured["next_offset"] is None:
            break
        assert f"offset {structured['next_offset']}" in result["content"][0]["text"]
        assert structured["markdown"].endswith("\n"), "a part does not split a line"
        offset = structured["next_offset"]
    assert len(parts) >= 3
    assert "".join(parts) == whole
    beyond = await _tool(client, token, "docs_read", slug="use/big", offset=len(whole) + 1)
    assert beyond["isError"] is True


async def test_an_unknown_page_is_refused(client: AsyncClient, admin: str, docs_dir: Path) -> None:
    token = await _token(client, "mcp:read")
    result = await _tool(client, token, "docs_read", slug="use/nowhere")
    assert result["isError"] is True
    assert "docs_search" in result["content"][0]["text"]


@pytest.mark.parametrize(
    "slug",
    [
        "../secret",
        "../secret.md",
        "../../documentation/secret",
        "use/../../secret",
        "/etc/passwd",
        "../../../../../../etc/passwd",
        "use/linked",
        "use/_partial",
        "%2e%2e/secret",
    ],
)
async def test_nothing_outside_the_documentation_is_read(
    client: AsyncClient, admin: str, docs_dir: Path, slug: str
) -> None:
    token = await _token(client, "mcp:read")
    result = await _tool(client, token, "docs_read", slug=slug)
    assert result["isError"] is True, result
    assert "quetzalcoatl" not in result["content"][0]["text"]


async def test_a_symbolic_link_and_a_partial_are_not_indexed(
    client: AsyncClient, admin: str, docs_dir: Path
) -> None:
    token = await _token(client, "mcp:read")
    for word in ("quetzalcoatl", "zanzibar"):
        pages = (await _tool(client, token, "docs_search", query=word))["structuredContent"]["pages"]
        assert pages == [], word


# ───────────────────────────── without documentation ─────────────────────────────


@pytest.mark.parametrize("kind", ["absent", "empty"])
async def test_a_build_without_documentation_says_so(
    client: AsyncClient, admin: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from choregos_api.config import reset_settings_cache

    directory = tmp_path / "no-docs"
    if kind == "empty":
        directory.mkdir()
    monkeypatch.setenv("CHOREGOS_DOCS_DIR", str(directory))
    monkeypatch.setenv("CHOREGOS_DOCS_URL", BASE)
    reset_settings_cache()
    token = await _token(client, "mcp:read")
    names = {o["name"] for o in (await _rpc(client, token, "tools/list")).json()["result"]["tools"]}
    assert {"docs_search", "docs_read"} <= names
    for name, arguments in (("docs_search", {"query": "workflow"}), ("docs_read", {"slug": ""})):
        result = await _tool(client, token, name, **arguments)
        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert "no bundled documentation" in text and BASE in text


# ───────────────────────────── the door's guards ─────────────────────────────


async def test_a_token_without_an_mcp_scope_is_refused(
    client: AsyncClient, admin: str, docs_dir: Path
) -> None:
    token = await _token(client, "*")
    response = await _rpc(client, token, "tools/call", {"name": "docs_search", "arguments": {"query": "x"}})
    assert response.status_code == 403, response.text
    anonymous = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "docs_read"}},
    )
    assert anonymous.status_code == 401


async def test_every_call_is_audited(client: AsyncClient, admin: str, docs_dir: Path) -> None:
    from choregos_api.db.models import AuditLog, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    token = await _token(client, "mcp:read")
    await _tool(client, token, "docs_search", query="kumquat")
    await _tool(client, token, "docs_read", slug="use/nowhere")
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "mcp.call").order_by(AuditLog.ts)
                )
            )
            .scalars()
            .all()
        )
        user = (await session.execute(select(User).where(User.email == ADMIN))).scalar_one()
    calls = [(r.target_type, r.target_id, r.payload["issue"], r.payload["write"], r.actor_id) for r in rows]
    assert calls == [
        ("mcp_tool", "docs_search", "ok", False, user.id),
        ("mcp_tool", "docs_read", "refused", False, user.id),
    ]
    await login(client, ADMIN)
    listed = (await client.get("/api/v1/audit")).json()["items"]
    assert {("mcp.call", "docs_search"), ("mcp.call", "docs_read")} <= {
        (a["action"], a.get("target_id")) for a in listed
    }


async def test_the_doors_rate_applies(
    client: AsyncClient, admin: str, docs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.mcp import transport
    from choregos_api.rate_limit import RateLimiter

    monkeypatch.setattr(transport, "RATE_LIMITER", RateLimiter(1))
    token = await _token(client, "mcp:read")
    first = await _rpc(client, token, "tools/call", {"name": "docs_search", "arguments": {"query": "gate"}})
    assert first.status_code == 200
    second = await _rpc(client, token, "tools/call", {"name": "docs_search", "arguments": {"query": "gate"}})
    assert second.status_code == 429


async def test_an_external_agent_reads_the_documentation_only_if_its_version_names_it(
    client: AsyncClient, admin: str, docs_dir: Path
) -> None:
    """The door's agent filter (ADR 0033) applies to the documentation tools like to any other."""
    from choregos_api.mcp.caller import Caller
    from choregos_api.mcp.tools import Context, announced_tools
    from choregos_api.rbac import Principal

    principal = Principal(user_id="u", email="u@example.test")
    caller = Caller(principal=principal, token=None, scopes=frozenset({"mcp:read"}), reasons=("list_*",))
    ctx = Context(session=None, caller=caller, project=None, org=None, console="")  # type: ignore[arg-type]
    names = {tool.name for tool in await announced_tools(ctx)}
    assert not names & {"docs_search", "docs_read"}
    caller.reasons = ("docs_*",)
    assert {tool.name for tool in await announced_tools(ctx)} == {"docs_search", "docs_read"}


# ───────────────────────────── the index ─────────────────────────────


def test_slugs_follow_the_public_site() -> None:
    from choregos_api.mcp.docs import slug_of

    assert slug_of(Path("index.md")) == ("", True)
    assert slug_of(Path("01-use/02-workflows.md")) == ("use/workflows", False)
    assert slug_of(Path("02-host/index.mdx")) == ("host", True)
    assert slug_of(Path("concepts/concepts.md")) == ("concepts", True)
    assert slug_of(Path("reference/README.md")) == ("reference", True)


def test_anchors_follow_docusaurus() -> None:
    from choregos_api.mcp.docs import Page, _sections, anchor_of

    assert anchor_of("Budgets & policies") == "budgets--policies"
    assert anchor_of("The `mcp:read` scope") == "the-mcpread-scope"
    assert anchor_of("[Links](https://x.test) *matter*") == "links-matter"
    markdown = "# A\n\n## Step\n\none\n\n## Step\n\ntwo\n\n## Named {#chosen}\n\nthree\n"
    anchors = [s.anchor for s in _sections(markdown)]
    assert anchors == ["a", "step", "step-1", "chosen"]
    assert Page(slug="x", title="X", markdown=markdown, is_index=False).url("https://d.test/c/") == (
        "https://d.test/c/x"
    )


def test_an_ontology_cannot_take_the_names_of_the_doors_tools() -> None:
    from choregos_api.mcp.caller import Caller
    from choregos_api.mcp.tools import Context, core_tools
    from choregos_api.rbac import Principal
    from choregos_ontology.mcpgen import RESERVED_TOOLS

    caller = Caller(principal=Principal(user_id="u", email="u@x.test"), token=None, scopes=frozenset())
    ctx = Context(session=None, caller=caller, project=None, org=None, console="")  # type: ignore[arg-type]
    assert {tool.name for tool in core_tools(ctx)} <= RESERVED_TOOLS
