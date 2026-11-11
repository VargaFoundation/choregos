# SPDX-License-Identifier: Apache-2.0
"""The public pages of S62-09b do what they say, against the real API (S62-09b).

Each test reads its page's own examples and sends them: the skill of `build/skills.md`, the agents and
the workflow of `build/agents.md` (run through the CLI's own `choregos workflow push`), the MCP server
and the policy of `build/tools.md`, the trigger of `integrate/triggers.md` and each answer its table
promises, the work item of `integrate/rest.md`, the token and the tool list of `integrate/mcp.md`, and
the refusal codes of `reference/errors.md` against the doors' code. A page whose example the API stops
taking turns this red.
"""

from __future__ import annotations

import io
import json
import re
import shlex
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import AsyncClient

from .site_pages import SITE, run_cli

FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```\s*$", re.M | re.S)
FAKE_TOOLS: list[dict[str, Any]] = [
    {"name": "search_docs", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "get_page", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "search", "inputSchema": {"type": "object", "properties": {"space": {"type": "string"}}},
     "annotations": {"readOnlyHint": True}},
]  # fmt: skip


def _page(name: str) -> str:
    return (SITE / name).read_text(encoding="utf-8")


def _blocks(name: str, language: str) -> list[str]:
    """A page's blocks in one language; `bash` also takes `shell`, the pages use both."""
    languages = {"bash", "shell"} if language == "bash" else {language}
    return [body for info, body in FENCE.findall(_page(name)) if info.split(" ")[0] in languages]


def _curls(name: str) -> list[tuple[str, str, Any]]:
    """Each `curl` of the page's shell blocks to `$CHOREGOS_API_URL`: its method, its path in the test's
    organisation, and its JSON body (None without one)."""
    found: list[tuple[str, str, Any]] = []
    for block in _blocks(name, "bash"):
        pending = ""
        for raw in block.replace("\\\n", " ").splitlines():
            pending = f"{pending}\n{raw}" if pending else raw.strip()
            try:
                words = shlex.split(pending)
            except ValueError:
                continue
            pending = ""
            if not words or words[0] != "curl":
                continue
            url = next(w for w in words if w.startswith("$CHOREGOS_API_URL"))
            body = words[words.index("-d") + 1] if "-d" in words else None
            method = words[words.index("-X") + 1] if "-X" in words else ("POST" if body else "GET")
            path = (
                url.removeprefix("$CHOREGOS_API_URL").replace("/acme/", "/varga/").replace("acme:", "varga:")
            )
            found.append((method, path, json.loads(body) if body else None))
    assert found, f"{name} shows no request any more"
    return found


def _command(name: str, prefix: str) -> list[str]:
    for block in _blocks(name, "bash"):
        for line in block.replace("\\\n", " ").splitlines():
            if line.startswith(prefix):
                return shlex.split(line)
    raise AssertionError(f"{name}: no `{prefix}` command")


async def _internal_tracker(client: AsyncClient, project: dict[str, Any]) -> None:
    answer = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert answer.status_code == 200, answer.text


async def _skill(client: AsyncClient) -> str:
    skill_md = next(b for b in _blocks("build/skills.md", "markdown") if b.startswith("---"))
    created = await client.post(
        "/api/v1/orgs/varga/skills",
        json={"files": {"SKILL.md": skill_md, "examples/good.md": "- Add credit notes (FIN-42)\n"}},
    )
    assert created.status_code == 201, created.text
    return skill_md


# ───────────────────────── build/skills.md ─────────────────────────


async def test_the_skill_of_the_page_is_taken_then_its_zip_is_the_next_version(
    client: AsyncClient, admin: str
) -> None:
    skill_md = await _skill(client)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("SKILL.md", skill_md)
        bundle.writestr("examples/good.md", "- Add credit notes (FIN-42)\n- Fix VAT rounding (FIN-40)\n")
    ((path, _),) = [
        (p, None) for p in re.findall(r'"\$CHOREGOS_API_URL(/api/v1/[^"]+)"', _page("build/skills.md"))
    ]
    imported = await client.post(
        path.replace("/acme/", "/varga/"),
        content=archive.getvalue(),
        headers={"Content-Type": "application/zip"},
    )
    assert imported.status_code in {200, 201}, imported.text
    listed = (await client.get("/api/v1/orgs/varga/skills/release-notes-style")).json()
    assert max(v["version"] for v in listed["versions"]) == 2, listed


async def test_the_skill_refusals_the_page_lists_are_the_api_s(client: AsyncClient, admin: str) -> None:
    table = _page("build/skills.md").split("## What can go wrong", 1)[1]
    bad = {
        "SKILL.md must start with its YAML front matter": "# no front matter\n",
        "`description` is missing": "---\nname: x-skill\n---\n",
        "a skill declares no permission": "---\nname: x-skill\ndescription: d\nallowed-tools: [Bash]\n---\n",
    }
    for message, text in bad.items():
        answer = await client.post("/api/v1/orgs/varga/skills", json={"files": {"SKILL.md": text}})
        assert answer.status_code == 422 and message in answer.text, answer.text
        assert message.strip("`").split("`")[0] in table
    reserved = await client.post(
        "/api/v1/orgs/varga/skills", json={"files": {"SKILL.md": "---\nname: library\ndescription: d\n---\n"}}
    )
    assert reserved.status_code == 422 and "skill.slug_reserved" in reserved.text, reserved.text
    assert "skill.slug_reserved" in table


# ───────────────────────── build/agents.md ─────────────────────────


async def test_from_nothing_to_an_agent_used_in_a_workflow(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path
) -> None:
    await _internal_tracker(client, project)
    await _skill(client)
    (_, create_path, prompt_agent), (_, publish_path, next_version) = _curls("build/agents.md")
    created = await client.post(create_path, json=prompt_agent)
    assert created.status_code == 201, created.text
    assert created.json()["slug"] == "release-notes"

    coding = next(json.loads(b) for b in _blocks("build/agents.md", "json") if '"implementer"' in b)
    assert (await client.post(create_path, json=coding)).status_code == 201

    workflow = next(b for b in _blocks("build/agents.md", "yaml") if "kind: Workflow" in b)
    (tmp_path / "release-notes.yaml").write_text(workflow, encoding="utf-8")
    for prefix in ("choregos workflow validate", "choregos workflow push"):
        ran = await run_cli(client, _command("build/agents.md", prefix), cwd=tmp_path)
        assert ran.exit_code == 0, ran.output
    listed = (await client.get(f"/api/v1/projects/{project['id']}/workflows")).json()
    assert "release-notes" in json.dumps(listed)

    item = await run_cli(client, _command("build/agents.md", "choregos items create"))
    assert item.exit_code == 0, item.output

    published = await client.post(publish_path, json=next_version)
    assert published.status_code in {200, 201}, published.text
    stale = await client.post(publish_path, json=next_version)
    assert stale.status_code == 409 and stale.json()["code"] == "stale_base", stale.text
    assert "stale_base" in _page("build/agents.md") and "no_change" in _page("build/agents.md")


def test_the_code_agent_s_registration_is_a_valid_agent() -> None:
    from choregos_api.schemas.agents import AgentCreate

    body = next(json.loads(b) for b in _blocks("build/agents.md", "json") if '"kind": "code"' in b)
    agent = AgentCreate.model_validate(body)
    assert agent.spec.runtime is not None and agent.spec.runtime.kind == "code"


# ───────────────────────── build/tools.md ─────────────────────────


async def test_the_server_the_grant_and_the_policy_of_the_page_are_taken(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    (_, path, server), (_, discover, _none), (patch_method, patch_path, opened) = _curls("build/tools.md")
    fake = FakeMcpServer(tools=[dict(t) for t in FAKE_TOOLS], token=server["secret_values"]["token"])
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", fake)
    declared = await client.post(path, json=server)
    assert declared.status_code == 201, declared.text
    assert server["secret_values"]["token"] not in declared.text, "a written secret is never returned"
    found = await client.post(discover)
    assert found.status_code == 200, found.text
    assert sorted(found.json()["added"]) == sorted(t["name"] for t in FAKE_TOOLS)
    operations = (await client.get(path + "/docs-search")).json()["operations"]
    assert {o["policy"] for o in operations} == {"forbidden"}, "the page says a new tool is born closed"
    answer = await client.request(patch_method, patch_path, json=opened)
    assert answer.status_code == 200, answer.text

    await _skill(client)
    (_, create_path, agent), _ = _curls("build/agents.md")
    assert (await client.post(create_path, json=agent)).status_code == 201
    for grant in (json.loads(b) for b in _blocks("build/tools.md", "json")):
        spec = {**agent["spec"], **grant}
        version = await client.post("/api/v1/orgs/varga/agents/release-notes/versions", json=spec)
        assert version.status_code in {200, 201}, version.text

    policy = next(b for b in _blocks("build/tools.md", "yaml") if "kind: Policy" in b)
    saved = await client.put(
        f"/api/v1/projects/{project['id']}/policy", json={"yaml": policy, "activate": True}
    )
    assert saved.status_code == 200, saved.text
    network = (await client.get(f"/api/v1/projects/{project['id']}/network")).json()
    assert "pypi.org" in json.dumps(network), network


# ───────────────────────── integrate/triggers.md ─────────────────────────


async def test_the_trigger_of_the_page_answers_as_its_table_says(client: AsyncClient, admin: str) -> None:
    from choregos_core.dsl import template_yaml

    triggers = json.loads(_blocks("integrate/triggers.md", "json")[0])
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": "billing-api",
            "name": "Billing API",
            "config": {"slug": "billing-api", "org": "varga", "triggers": triggers},
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    opt_in = yaml.safe_load(
        re.search(r"```yaml\n(metadata:.*?)```", _page("integrate/a2a.md"), re.S).group(1)
    )  # type: ignore[union-attr]
    workflow = yaml.safe_load(template_yaml("default-simple"))
    workflow["metadata"].update(name="ship-a-change", inputs=opt_in["metadata"]["inputs"])
    published = await client.put(
        f"/api/v1/projects/{project['id']}/workflows/ship-a-change",
        json={"yaml": yaml.safe_dump(workflow, sort_keys=False)},
    )
    assert published.status_code in {200, 201}, published.text
    minted = await client.post(f"/api/v1/projects/{project['id']}/triggers/error-alert/token")
    assert minted.status_code == 201 and minted.json()["token"].startswith("chtrg_"), minted.text
    url = re.search(
        r"POST https://choregos\.example\.com(/api/v1/webhooks/triggers/\S+)", _page("integrate/triggers.md")
    )
    assert url and minted.json()["url"].endswith(url.group(1).replace("/acme/", "/varga/"))
    client.cookies.clear()

    body = json.loads(re.search(r"-d '(\{.*?^\})'", _page("integrate/triggers.md"), re.M | re.S).group(1))  # type: ignore[union-attr]
    headers = {"Authorization": f"Bearer {minted.json()['token']}"}
    target = url.group(1).replace("/acme/", "/varga/")

    first = await client.post(target, json=body, headers=headers)
    assert first.status_code == 202, first.text
    sample = json.loads(_blocks("integrate/triggers.md", "json")[1])
    assert set(sample) == set(first.json()) and first.json()["outcome"] == sample["outcome"]
    assert first.json()["workflow"] == "ship-a-change"
    again = await client.post(target, json=body, headers=headers)
    assert again.json()["duplicate"] is True, again.text
    still = await client.post(target, json=body, headers={**headers, "Idempotency-Key": "second-delivery"})
    assert still.json()["outcome"] == "already_open", still.text
    quiet = await client.post(target, json={**body, "id": "5a", "level": "warning"}, headers=headers)
    assert quiet.json()["outcome"] == "ignored", quiet.text
    wrong = await client.post(target, json=body, headers={"Authorization": "Bearer chtrg_wrong"})
    assert wrong.status_code == 401

    await _login_back(client)
    item = (await client.get(f"/api/v1/work-items/{first.json()['work_item_id']}")).json()
    assert item["title"] == "Fix: ZeroDivisionError in invoice totals", item
    assert item["fields"] == {"ticket": "ERR-311", "priority": "high"}, item
    table = _page("integrate/triggers.md")
    for outcome in ("created_work_item", "already_open", "reopened", "ignored"):
        assert f"`{outcome}`" in table


async def _login_back(client: AsyncClient) -> None:
    from .conftest import login

    await login(client, "admin@varga.dev")


# ───────────────────────── integrate/rest.md ─────────────────────────


async def test_the_rest_page_s_requests_work_with_a_full_access_token(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_core.dsl import template_yaml

    await _internal_tracker(client, project)
    opt_in = yaml.safe_load(
        re.search(r"```yaml\n(metadata:.*?)```", _page("integrate/a2a.md"), re.S).group(1)
    )  # type: ignore[union-attr]
    workflow = yaml.safe_load(template_yaml("default-simple"))
    workflow["metadata"].update(name="ship-a-change", inputs=opt_in["metadata"]["inputs"])
    await client.put(
        f"/api/v1/projects/{project['id']}/workflows/ship-a-change",
        json={"yaml": yaml.safe_dump(workflow, sort_keys=False)},
    )
    tokens = await run_cli(client, _command("integrate/rest.md", "choregos tokens create"))
    assert tokens.exit_code == 0, tokens.output
    token = tokens.output.strip().splitlines()[-1].strip()
    client.cookies.clear()
    auth = {"Authorization": f"Bearer {token}"}

    (_, path, body), *_reads = _curls("integrate/rest.md")
    created = await client.post(path, json=body, headers=auth)
    assert created.status_code == 201, created.text
    key = created.json()["tracker_key"]
    assert re.match(r"^[A-Z][A-Z0-9-]*-\d+$", key), key
    read = await client.get(f"/api/v1/work-items/{key}", headers=auth)
    assert read.status_code == 200 and read.json()["id"] == created.json()["id"]
    for field in ("state", "state_display", "pending_request", "pr_url", "failure", "totals"):
        assert field in read.json(), field
    listed = (await client.get("/api/v1/projects/varga:billing-api/work-items?limit=20", headers=auth)).json()
    assert set(listed) == {"items", "meta"} and {"next_cursor", "has_more"} <= set(listed["meta"])
    refused = await client.post(path, json={"workflow": "ship-a-change"}, headers=auth)
    assert refused.status_code == 422 and refused.json()["title"] == "Invalid request", refused.text
    assert "Invalid request" in _page("integrate/rest.md")


# ───────────────────────── integrate/mcp.md ─────────────────────────


async def test_the_mcp_page_s_token_opens_the_door_and_the_tools_are_its_table(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    minted = await run_cli(client, _command("integrate/mcp.md", "choregos tokens create"))
    assert minted.exit_code == 0, minted.output
    token = minted.output.strip().splitlines()[-1].strip()
    client.cookies.clear()
    listed = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listed.status_code == 200, listed.text
    served = {tool["name"] for tool in listed.json()["result"]["tools"]}
    table = _page("integrate/mcp.md").split("## The door and its tools", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", table, re.M))
    assert served == documented, (served, documented)
    from choregos_core.mcp import VERSIONS

    for version in VERSIONS:
        assert f"`{version}`" in _page("integrate/mcp.md")


# ───────────────────────── reference/errors.md ─────────────────────────


def test_the_a2a_codes_of_the_errors_page_are_the_door_s() -> None:
    from choregos_api.a2a import door, push, v1

    codes = {
        value
        for module in (door, push, v1)
        for value in vars(module).values()
        if isinstance(value, int) and not isinstance(value, bool) and -32768 <= value <= -32000
    }
    section = _page("reference/errors.md").split("## The A2A door", 1)[1].split("\n## ", 1)[0]
    listed = {int(code) for code in re.findall(r"^\| `(-32\d\d\d)` \|", section, re.M)}
    assert listed == codes, (sorted(listed - codes), sorted(codes - listed))


def test_the_mcp_door_s_limits_are_the_pages() -> None:
    from choregos_api.mcp import limits

    for page in ("integrate/mcp.md", "reference/errors.md"):
        text = _page(page)
        assert f"{limits.CALLS_PER_MINUTE} calls a minute" in text
        assert f"{limits.MAX_SIZE:,} characters" in text or page == "reference/errors.md"
    assert f"{limits.WRITES_PER_DAY} writes a day" in _page("integrate/mcp.md")
