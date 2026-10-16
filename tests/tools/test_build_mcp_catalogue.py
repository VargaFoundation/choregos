"""The MCP catalogue generator: replayable, byte-deterministic, and strict about what it drops (ADR 0065)."""

from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

import httpx
import pytest
from choregos_core import mcp_catalogue

from tools.catalogues import _github, _recording
from tools.catalogues import build_mcp_catalogue as gen

SEED = gen._load_yaml("seed.yaml")
OVERRIDES = gen._load_yaml("overrides.yaml")
EXCLUSIONS = gen._load_yaml("exclusions.yaml")


def _registry_record(
    name: str, *, status: str = "active", remote: str | None = None, headers: Any = None
) -> dict[str, Any]:
    server: dict[str, Any] = {"name": name, "version": "1.2.3", "description": "x"}
    if remote:
        server["remotes"] = [{"type": "streamable-http", "url": remote, "headers": headers or []}]
    return {
        "server": server,
        "_meta": {"io.modelcontextprotocol.registry/official": {"status": status}},
    }


def _facts(
    owner_repo: str, *, archived: bool = False, licence: str | None = "MIT", stars: int = 500
) -> _github.RepoFacts:
    return _github.RepoFacts(full_name=owner_repo, stars=stars, archived=archived, licence=licence)


def _line(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "demo",
        "title": "Demo",
        "publisher": "Demo Inc",
        "description": "A demo server.",
        "categories": ["developer-tools"],
        "auth": "bearer",
        "registry": "com.demo/mcp",
        "repo": "demo/mcp",
    }
    return base | kw


def _resolve(
    line: dict[str, Any], *, record: dict[str, Any] | None, facts: _github.RepoFacts | None, **over: Any
) -> Any:
    registry = {line["registry"]: record} if line.get("registry") else {}
    github = {line["repo"]: facts} if line.get("repo") else {}
    overrides = {"remotes": {}, "licences": {}, "vendors": {}} | over
    return gen.resolve(line, registry, github, overrides, EXCLUSIONS, "2026-10-15")


# ---- the committed file is what the recording yields


def test_replaying_the_recordings_reproduces_the_committed_file_byte_for_byte() -> None:
    meta = gen.read_meta(gen.FIXTURES)
    with _recording.make_client(gen.FIXTURES, record=False) as client:
        registry = gen.fetch_registry(client, [s["registry"] for s in SEED if s.get("registry")])
        github = _github.fetch_repo_facts(client, [s["repo"] for s in SEED if s.get("repo")])
    document, drops = gen.build(SEED, registry, github, OVERRIDES, EXCLUSIONS, meta["recorded_at"])
    assert drops == [], drops
    assert gen.render(document) == gen.OUTPUT.read_text("utf-8")


def test_the_check_mode_fails_on_a_stale_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed = gen.OUTPUT
    assert gen.main(["--check"]) == 0
    stale = tmp_path / "catalogue.yaml"
    stale.write_text(committed.read_text("utf-8").replace("id: github", "id: gitlab-x", 1), "utf-8")
    monkeypatch.setattr(gen, "OUTPUT", stale)
    assert gen.main(["--check"]) == 1


# ---- the rules in code


def test_a_deleted_registry_entry_is_dropped() -> None:
    result = _resolve(
        _line(), record=_registry_record("com.demo/mcp", status="deleted"), facts=_facts("demo/mcp")
    )
    assert isinstance(result, gen.Dropped) and "not active" in result.reason


def test_a_name_the_registry_does_not_know_is_dropped() -> None:
    result = _resolve(_line(), record=None, facts=_facts("demo/mcp"))
    assert isinstance(result, gen.Dropped) and "not in the registry" in result.reason


def test_an_archived_repository_is_dropped() -> None:
    result = _resolve(
        _line(), record=_registry_record("com.demo/mcp"), facts=_facts("demo/mcp", archived=True)
    )
    assert isinstance(result, gen.Dropped) and "archived" in result.reason


def test_a_missing_repository_is_dropped() -> None:
    result = _resolve(_line(), record=_registry_record("com.demo/mcp"), facts=None)
    assert isinstance(result, gen.Dropped) and "not found" in result.reason


@pytest.mark.parametrize(
    "url",
    [
        "https://abc.trycloudflare.com/mcp",
        "https://x.ngrok-free.app/mcp",
        "https://thing.workers.dev/mcp",
        "https://x.smithery.ai/mcp",
    ],
)
def test_a_tunnel_or_relay_host_is_dropped(url: str) -> None:
    result = _resolve(_line(), record=_registry_record("com.demo/mcp", remote=url), facts=_facts("demo/mcp"))
    assert isinstance(result, gen.Dropped) and "remote host" in result.reason


def test_a_plain_http_remote_and_a_templated_remote_are_dropped() -> None:
    plain = _resolve(
        _line(),
        record=_registry_record("com.demo/mcp", remote="http://demo.example/mcp"),
        facts=_facts("demo/mcp"),
    )
    templated = _resolve(
        _line(),
        record=_registry_record("com.demo/mcp", remote="https://{tenant}.demo.example/mcp"),
        facts=_facts("demo/mcp"),
    )
    assert isinstance(plain, gen.Dropped) and "TLS" in plain.reason
    assert isinstance(templated, gen.Dropped) and "variables" in templated.reason


def test_a_stdio_server_without_an_open_licence_is_dropped() -> None:
    line = _line(registry=None, package="npm:demo")
    assert isinstance(_resolve(line, record=None, facts=_facts("demo/mcp", licence=None)), gen.Dropped)
    assert isinstance(
        _resolve(line, record=None, facts=_facts("demo/mcp", licence="LicenseRef-Proprietary")), gen.Dropped
    )
    kept = _resolve(line, record=None, facts=_facts("demo/mcp", licence="Apache-2.0"))
    assert kept["transport"] == "stdio" and kept["hosting"] == {"package": "npm:demo", "version": None}


def test_a_seed_naming_something_the_platform_never_offers_is_dropped() -> None:
    for title in (
        "Desktop Commander",
        "Terminal runner",
        "Anti-detect browser",
        "x402 payments",
        "Crypto wallet",
    ):
        result = _resolve(
            _line(title=title), record=_registry_record("com.demo/mcp"), facts=_facts("demo/mcp")
        )
        assert isinstance(result, gen.Dropped) and result.reason.startswith("excluded"), title


def test_the_overrides_add_a_vendor_remote_the_registry_lacks() -> None:
    over = {"remotes": {"demo": {"url": "https://api.demo.example/mcp", "protocol": "streamable-http"}}}
    result = _resolve(_line(), record=_registry_record("com.demo/mcp"), facts=_facts("demo/mcp"), **over)
    assert result["remote"] == {"url": "https://api.demo.example/mcp", "protocol": "streamable-http"}
    assert result["egress_hosts"] == ["api.demo.example"]


def test_a_registry_that_requires_a_bearer_contradicts_a_seed_saying_none() -> None:
    record = _registry_record(
        "com.demo/mcp",
        remote="https://demo.example/mcp",
        headers=[{"name": "Authorization", "isRequired": True}],
    )
    with pytest.raises(gen.GeneratorError, match="requires an Authorization header"):
        _resolve(_line(auth="none"), record=record, facts=_facts("demo/mcp"))


def test_official_comes_from_the_vendor_map_never_from_the_seed() -> None:
    record = _registry_record("com.demo/mcp", remote="https://mcp.demo.com/mcp")
    vendors = {"demo": {"domains": ["demo.com"], "orgs": ["demo"]}}
    assert (
        _resolve(_line(official=True), record=record, facts=_facts("demo/mcp"), vendors={})["official"]
        is False
    )
    assert _resolve(_line(), record=record, facts=_facts("demo/mcp"), vendors=vendors)["official"] is True
    # a lookalike namespace and a lookalike org are not the vendor
    fake = _registry_record("com.demo-fake/mcp", remote="https://mcp.demo-fake.com/mcp")
    assert (
        _resolve(
            _line(registry="com.demo-fake/mcp", repo="evil/mcp"),
            record=fake,
            facts=_facts("evil/mcp"),
            vendors=vendors,
        )["official"]
        is False
    )


def test_stars_of_a_monorepo_are_not_reported() -> None:
    record = _registry_record("com.demo/mcp", remote="https://mcp.demo.com/mcp")
    result = _resolve(_line(stars=False), record=record, facts=_facts("demo/mcp", stars=190000))
    assert result["stars"] is None and result["stars_as_of"] is None


def test_a_duplicate_seed_id_is_refused() -> None:
    with pytest.raises(gen.GeneratorError, match="duplicate ids"):
        gen.build([_line(), _line()], {}, {}, {"catalogue_version": "2026.10.0"}, EXCLUSIONS, "2026-10-15")


def test_the_output_does_not_depend_on_the_order_of_the_seed() -> None:
    meta = gen.read_meta(gen.FIXTURES)
    with _recording.make_client(gen.FIXTURES, record=False) as client:
        registry = gen.fetch_registry(client, [s["registry"] for s in SEED if s.get("registry")])
        github = _github.fetch_repo_facts(client, [s["repo"] for s in SEED if s.get("repo")])
    shuffled = list(reversed(copy.deepcopy(SEED)))
    a, _ = gen.build(SEED, registry, github, OVERRIDES, EXCLUSIONS, meta["recorded_at"])
    b, _ = gen.build(shuffled, registry, github, OVERRIDES, EXCLUSIONS, meta["recorded_at"])
    assert gen.render(a) == gen.render(b)


# ---- the recording layer


def test_replay_refuses_the_network_when_nothing_was_recorded(tmp_path: pathlib.Path) -> None:
    with (
        _recording.make_client(tmp_path, record=False) as client,
        pytest.raises(_recording.RecordingMissError, match="--record"),
    ):
        client.get("https://registry.modelcontextprotocol.io/v0.1/servers/x")


def test_a_recording_is_replayed_without_its_authorization_header(tmp_path: pathlib.Path) -> None:
    class Canned(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True}, request=request)

    recorder = _recording.RecordTransport(tmp_path, Canned())
    with httpx.Client(transport=recorder, headers={"Authorization": "Bearer secret-token"}) as client:
        assert client.post("https://api.example/graphql", json={"q": 1}).json() == {"ok": True}
    written = [p.read_text("utf-8") for p in tmp_path.glob("*.json")]
    assert len(written) == 1 and "secret-token" not in written[0]
    with _recording.make_client(tmp_path, record=False) as client:
        assert client.post("https://api.example/graphql", json={"q": 1}).json() == {"ok": True}
        with pytest.raises(_recording.RecordingMissError):
            client.post("https://api.example/graphql", json={"q": 2})


def test_the_graphql_query_is_batched_and_a_missing_repository_is_none(tmp_path: pathlib.Path) -> None:
    seen: list[str] = []

    class Fake(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content)["query"])
            data = {
                "r0": {
                    "nameWithOwner": "a/b",
                    "stargazerCount": 7,
                    "isArchived": False,
                    "licenseInfo": {"spdxId": "NOASSERTION"},
                },
                "r1": None,
            }
            return httpx.Response(200, json={"data": data}, request=request)

    with httpx.Client(transport=Fake()) as client:
        facts = _github.fetch_repo_facts(client, ["https://github.com/a/b", "a/gone"])
    assert facts["a/b"] == _github.RepoFacts("a/b", 7, False, None)
    assert facts["a/gone"] is None
    assert len(seen) == 1


def test_the_console_demo_data_is_the_shipped_catalogue_as_the_api_shows_it() -> None:
    """`apps/web/src/mocks/mcp-catalogue.json` is generated, never typed: the e2e runs on the real entries."""
    mock = json.loads(gen.WEB_MOCK.read_text("utf-8"))
    shipped = mcp_catalogue.load()
    assert mock["meta"]["catalogue_version"] == shipped.meta.catalogue_version
    assert [e["id"] for e in mock["entries"]] == [e.id for e in shipped.entries]
    by_id = {e["id"]: e for e in mock["entries"]}
    assert by_id["github"]["install"] == "installable" and by_id["notion"]["install"] == "needs_oauth"
    assert {e["state"] for e in mock["entries"]} == {"available"} and {
        e["egress"] for e in mock["entries"]
    } == {"unknown"}
