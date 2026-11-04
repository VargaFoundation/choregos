"""Tool catalogue: the key stays on the platform side, and the URL is never free."""

from __future__ import annotations

import logging

import pytest
from choregos_core import catalogue as catalogue_module
from choregos_core.catalogue import ArgumentRefusedError, build_request, load_catalogue

CATALOGUE = """
tools:
  - name: search_profiles
    description: Searches profiles matching criteria.
    provider: directory
    categories: [hr]
    input_schema:
      type: object
      required: [trade]
      properties:
        trade: { type: string }
        city: { type: string }
    http:
      method: GET
      url: https://api.directory.example/v1/search
      query: { q: "{{ trade }}", city: "{{ city }}" }
      headers: { Authorization: "Bearer {{ credential }}" }
    credential_env: DIRECTORY_API_KEY
    price_eur: 0.02
"""


def test_the_key_appears_only_in_the_outgoing_request() -> None:
    """The catalogue is read in a PR: it carries the NAME of the variable, never the key.
    And the key rendered here goes neither back to the agent, nor into the log."""
    tool = load_catalogue(CATALOGUE).by_name("search_profiles")
    assert tool is not None
    assert tool.credential_env == "DIRECTORY_API_KEY"
    assert "secret" not in CATALOGUE.lower()

    request = build_request(tool, {"trade": "data engineer", "city": "Lille"}, "vault-key")
    assert request["headers"]["Authorization"] == "Bearer vault-key"
    assert request["params"] == {"q": "data engineer", "city": "Lille"}
    assert request["url"] == "https://api.directory.example/v1/search"


def test_a_missing_argument_is_refused_rather_than_left_empty() -> None:
    """Filling a placeholder with an empty string would send a request that looks valid
    and searches for nothing: the agent would believe in an empty result."""
    tool = load_catalogue(CATALOGUE).by_name("search_profiles")
    assert tool is not None
    with pytest.raises(ArgumentRefusedError, match="city"):
        build_request(tool, {"trade": "data engineer"}, "key")


def test_the_agent_chooses_neither_the_url_nor_the_method() -> None:
    """A tool whose URL the agent set would make the platform an open proxy,
    calling anything with its own keys."""
    tool = load_catalogue(CATALOGUE).by_name("search_profiles")
    assert tool is not None
    request = build_request(tool, {"trade": "x", "city": "y", "url": "http://169.254.169.254/"}, "key")
    assert request["url"].startswith("https://api.directory.example/")
    assert request["method"] == "GET"


REMOTE_MCP = """
tools:
  - name: search_company
    description: Searches a company at a third-party provider.
    provider: external-directory
    groups: [hr, sales]
    input_schema: { type: object, required: [siren], properties: { siren: { type: string } } }
    mcp:
      url: https://mcp.provider.example/mcp
      tool: company_lookup
      headers: { Authorization: "Bearer {{ credential }}" }
    credential_env: PROVIDER_MCP_KEY
"""


def test_a_tool_has_one_source_and_only_one() -> None:
    """With neither it does nothing; with both one would not know which to use. Saying so
    at load time beats saying so at the first call."""
    with pytest.raises(ValueError, match="must declare"):
        load_catalogue("tools:\n  - {name: empty, description: d, provider: p}")

    both = REMOTE_MCP.replace(
        "    credential_env: PROVIDER_MCP_KEY",
        "    credential_env: PROVIDER_MCP_KEY\n    http: { url: https://example.test }",
    )
    with pytest.raises(ValueError, match="must declare"):
        load_catalogue(both)


def test_the_remote_name_is_not_the_one_the_agent_sees() -> None:
    """The indirection is not cosmetic: it makes it possible to expose only PART of a
    server's tools, and to name them in the house vocabulary."""
    tool = load_catalogue(REMOTE_MCP).by_name("search_company")
    assert tool is not None and tool.mcp is not None
    assert tool.mcp.tool == "company_lookup", "the name at the provider"
    assert tool.name == "search_company", "the name here"
    assert tool.http is None


def test_a_tool_reserved_to_groups_is_not_open_to_everyone() -> None:
    """The deployment says WHO may; the project says what IT uses. Without this
    lock, the project's list would be the only control, and the project's own
    team can edit it."""
    tool = load_catalogue(REMOTE_MCP).by_name("search_company")
    assert tool is not None
    assert tool.open_to(["hr"])
    assert tool.open_to(["sales", "other"])
    assert not tool.open_to(["legal"])
    assert not tool.open_to([])


def test_without_a_declared_group_the_tool_restricts_nothing() -> None:
    """A catalogue without groups stays usable as it is: the restriction is added on top."""
    tool = load_catalogue(CATALOGUE).by_name("search_profiles")
    assert tool is not None and tool.groups == []
    assert tool.open_to([]) and tool.open_to(["anything"])


# ──────── what the agent sends does not choose the target (2026-09-27) ────────

PATH_TEMPLATE = """
tools:
  - name: read_a_repository
    description: Reads the public metadata of a GitHub repository.
    provider: github
    input_schema:
      type: object
      required: [owner, repo]
      properties:
        owner: { type: string, pattern: "^[A-Za-z0-9._-]+$" }
        repo: { type: string, pattern: "^[A-Za-z0-9._-]+$" }
    http:
      method: GET
      url: https://api.github.com/repos/{{ owner }}/{{ repo }}
    price_eur: 0.0
"""


def test_an_argument_cannot_climb_the_url_path() -> None:
    """The catalogue exists so that the agent does NOT choose the URL (ADR 0014).

    The host is written in the catalogue, so it does not move. But while the value was
    substituted bare, `owner` set to `..` climbed one segment: a tool meant to read
    `/repos/<org>/<repository>` reached any other entry point of the same host, **with the
    provider's key that the platform attaches there**. The header of this file always claimed
    that "the URL is never free"; it was, in part.
    """
    tool = load_catalogue(PATH_TEMPLATE).by_name("read_a_repository")
    assert tool is not None

    # Encoding alone was NOT enough, and that is the trap: `quote("..")` gives `..`, the dot
    # being an unreserved character. `/repos/../user` normalises to `/user`, another entry
    # point of the same host. It is the comparison with the prefix that refuses.
    with pytest.raises(ArgumentRefusedError) as refused:
        build_request(tool, {"owner": "..", "repo": "user"}, None)
    assert "left the tool's path" in str(refused.value), refused.value

    # A value that tried to add a segment, a query or a fragment is encoded.
    request = build_request(tool, {"owner": "org", "repo": "d?token=x#y"}, None)
    assert request["url"] == "https://api.github.com/repos/org/d%3Ftoken%3Dx%23y"
    request = build_request(tool, {"owner": "org/other", "repo": "d"}, None)
    assert request["url"] == "https://api.github.com/repos/org%2Fother/d"

    # And the nominal case passes, unchanged.
    request = build_request(tool, {"owner": "VargaFoundation", "repo": "choregos"}, None)
    assert request["url"] == "https://api.github.com/repos/VargaFoundation/choregos"


def test_an_argument_outside_the_schema_is_refused_before_the_call() -> None:
    """`input_schema` was DECORATIVE: announced to the agent, never checked.

    It is the `inputSchema` that `GET /internal/runs/{id}/tools` returns in MCP format. An agent
    had every reason to believe it binding. A schema that is announced and not enforced describes
    a guarantee that does not exist.
    """
    from choregos_core.catalogue import validate_arguments

    tool = load_catalogue(PATH_TEMPLATE).by_name("read_a_repository")
    assert tool is not None

    validate_arguments(tool, {"owner": "VargaFoundation", "repo": "choregos"})  # nominal

    with pytest.raises(ArgumentRefusedError) as refused:
        validate_arguments(tool, {"owner": "../..", "repo": "choregos"})
    assert "owner" in str(refused.value), f"the refusal must name the argument: {refused.value}"

    with pytest.raises(ArgumentRefusedError):
        validate_arguments(tool, {"owner": "org"})  # `repo` required and missing

    with pytest.raises(ArgumentRefusedError):
        validate_arguments(tool, {"owner": 42, "repo": "choregos"})  # wrong type


def test_a_tool_without_a_schema_imposes_nothing() -> None:
    """The catalogue must stay writable by hand for a trivial tool."""
    from choregos_core.catalogue import validate_arguments

    tool = load_catalogue(CATALOGUE).by_name("search_profiles")
    assert tool is not None
    tool.input_schema = {}
    validate_arguments(tool, {"anything": "at all"})


def test_a_catalogue_tool_declares_its_result_cap_and_no_cap_below_one_kibibyte() -> None:
    """S40-17: an entry may say how much of its answer the agent reads; a cap so small that the
    truncation marker would not fit is refused at load, not at the first call."""
    declared = CATALOGUE + "    result_max_bytes: 4096\n"
    assert load_catalogue(declared).tools[0].result_max_bytes == 4096
    assert load_catalogue(CATALOGUE).tools[0].result_max_bytes is None
    with pytest.raises(ValueError, match="result_max_bytes"):
        load_catalogue(CATALOGUE + "    result_max_bytes: 512\n")


def test_catalogue_reads_tools_and_former_outils_key(  # lang: fr-ok
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The operators' YAML key is `tools:`; the former `outils:` still loads the same tools, with a
    one-time warning that it is deprecated until 0.23.0, and a bare list maps to the tools."""
    former = CATALOGUE.replace("tools:", "outils:", 1)  # lang: fr-ok
    catalogue_module._former_key_warned.clear()
    with caplog.at_level(logging.WARNING, logger="choregos.catalogue"):
        from_former = load_catalogue(former)
        load_catalogue(former)  # the second read does not warn again
    assert from_former.tools == load_catalogue(CATALOGUE).tools
    assert [t.name for t in from_former.tools] == ["search_profiles"]
    warnings = [r for r in caplog.records if "deprecated" in r.getMessage()]
    assert len(warnings) == 1 and "0.23.0" in warnings[0].getMessage()

    caplog.clear()
    assert load_catalogue(CATALOGUE).tools  # the new key does not warn
    assert not caplog.records

    as_a_list = "- {name: bare_list, description: d, provider: p, http: {url: 'https://example.test'}}\n"
    assert [t.name for t in load_catalogue(as_a_list).tools] == ["bare_list"]

    with pytest.raises(ValueError, match="not both"):
        load_catalogue("tools: []\noutils: []\n")  # lang: fr-ok
