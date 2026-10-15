"""The shipped MCP catalogue honours its schema and the rules that make it safe to offer (ADR 0065)."""

from __future__ import annotations

import copy
import pathlib
from collections import Counter
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml
from choregos_core import mcp_catalogue
from choregos_core.catalogues import CatalogueError, read_catalogue
from choregos_core.mcp_catalogue import McpEntry, install_kind

ROOT = pathlib.Path(__file__).resolve().parents[3]
EXCLUSIONS = yaml.safe_load((ROOT / "tools/catalogues/mcp/exclusions.yaml").read_text("utf-8"))
OVERRIDES = yaml.safe_load((ROOT / "tools/catalogues/mcp/overrides.yaml").read_text("utf-8"))


def _host_matches(host: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        return host == pattern[2:] or host.endswith("." + pattern[2:])
    return host == pattern


def test_the_file_validates_and_ids_are_unique() -> None:
    catalogue = mcp_catalogue.load()
    ids = [e.id for e in catalogue.entries]
    assert len(ids) == len(set(ids))
    assert catalogue.meta.catalogue_version == OVERRIDES["catalogue_version"]


def test_the_catalogue_holds_about_a_hundred_entries() -> None:
    assert 90 <= len(mcp_catalogue.load().entries) <= 130


def test_every_installable_entry_has_an_https_remote_and_its_egress_host() -> None:
    installable = [e for e in mcp_catalogue.load().entries if e.install == "installable"]
    assert len(installable) >= 15
    for e in installable:
        assert e.remote is not None, e.id
        parsed = urlparse(e.remote.url)
        assert parsed.scheme == "https", e.id
        assert e.remote.protocol == "streamable-http", e.id
        assert e.egress_hosts == [parsed.hostname], e.id
        assert e.auth in {"none", "bearer"} or e.auth.startswith("header:"), e.id


def test_no_entry_sits_on_an_excluded_host() -> None:
    for e in mcp_catalogue.load().entries:
        for host in e.egress_hosts:
            for rule in EXCLUSIONS["hosts"]:
                assert not _host_matches(host, rule["pattern"]), f"{e.id}: {host} ({rule['reason']})"


def test_an_official_entry_has_a_vendor_in_the_map() -> None:
    official = [e for e in mcp_catalogue.load().entries if e.official]
    assert official
    for e in official:
        assert e.id in OVERRIDES["vendors"], f"{e.id} is official but names no vendor"


def test_stars_always_carry_their_date() -> None:
    for e in mcp_catalogue.load().entries:
        assert (e.stars is None) == (e.stars_as_of is None), e.id


def test_a_stdio_entry_names_what_to_run_and_an_open_licence() -> None:
    stdio = [e for e in mcp_catalogue.load().entries if e.transport == "stdio"]
    assert stdio
    for e in stdio:
        assert e.hosting is not None and e.hosting.package, e.id
        assert e.remote is None, e.id
        assert e.licence is not None, e.id


def test_nothing_in_the_file_enables_anything() -> None:
    """An entry is an offer: no field of the file can switch a connector or an operation on."""
    forbidden = {"enabled", "operations", "policy", "install", "state", "default"}
    assert forbidden.isdisjoint(McpEntry.model_fields)
    raw = yaml.safe_load(mcp_catalogue.CATALOGUE_PATH.read_text("utf-8"))
    for entry in raw["entries"]:
        assert forbidden.isdisjoint(entry), entry["id"]


def test_install_is_computed_from_the_code_not_stored() -> None:
    entry = copy.deepcopy(mcp_catalogue.load().by_id("notion"))
    assert entry is not None and entry.install == "needs_oauth"
    assert install_kind(entry.model_copy(update={"auth": "bearer"})) == "installable"
    assert install_kind(entry.model_copy(update={"transport": "stdio", "remote": None})) == "needs_hosting"
    sse = entry.model_copy(update={"remote": entry.remote.model_copy(update={"protocol": "sse"})})  # type: ignore[union-attr]
    assert install_kind(sse) == "needs_hosting"


def test_the_three_kinds_are_all_present() -> None:
    assert set(Counter(e.install for e in mcp_catalogue.load().entries)) == {
        "installable",
        "needs_oauth",
        "needs_hosting",
    }


def _write(tmp_path: pathlib.Path, mutate: Any) -> pathlib.Path:
    raw = yaml.safe_load(mcp_catalogue.CATALOGUE_PATH.read_text("utf-8"))
    mutate(raw)
    path = tmp_path / "catalogue.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), "utf-8")
    return path


def test_a_duplicate_id_is_refused(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, lambda raw: raw["entries"].append(copy.deepcopy(raw["entries"][0])))
    with pytest.raises(CatalogueError, match="duplicate id"):
        read_catalogue(path, mcp_catalogue.SCHEMA_PATH)


def test_an_unknown_category_an_http_remote_or_a_long_description_is_refused(tmp_path: pathlib.Path) -> None:
    def break_it(raw: dict[str, Any]) -> None:
        raw["entries"][0]["categories"] = ["not-a-category"]
        raw["entries"][1]["remote"] = {"url": "http://insecure.example/mcp", "protocol": "streamable-http"}
        raw["entries"][2]["description"] = "x" * 301
        raw["entries"][3]["unexpected"] = True

    with pytest.raises(CatalogueError) as excinfo:
        read_catalogue(_write(tmp_path, break_it), mcp_catalogue.SCHEMA_PATH)
    message = str(excinfo.value)
    assert (
        "categories" in message
        and "remote" in message
        and "description" in message
        and "unexpected" in message
    )


def test_a_catalogue_version_must_be_a_calver(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, lambda raw: raw.update(catalogue_version="latest"))
    with pytest.raises(CatalogueError, match="CalVer"):
        read_catalogue(path, mcp_catalogue.SCHEMA_PATH)
