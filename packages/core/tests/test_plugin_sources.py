# SPDX-License-Identifier: Apache-2.0
"""Plugin sources without the network (ADR 0093 §4, S43-03): ranges, configuration, indexes,
resolution against the core, the offline directory and its lock, a git tarball's extraction."""

from __future__ import annotations

import io
import shutil
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_core.plugins import digest, pack
from choregos_core.plugins.sources import (
    LOCK,
    SourceError,
    extract_git_path,
    listing,
    offline_index,
    parse_config,
    parse_index,
    resolve,
)
from choregos_core.plugins.versions import Range, Version, VersionError, admits, core_version

HELLO = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "hello"
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64


def _check(caught: pytest.ExceptionInfo[SourceError]) -> str:
    return caught.value.check


# -- versions -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("range_text", "inside", "outside"),
    [
        ("^1.2", ["1.2.0", "1.9.9"], ["1.1.9", "2.0.0"]),
        ("^0.2.3", ["0.2.3", "0.2.9"], ["0.3.0", "0.2.2"]),
        ("^0.0.3", ["0.0.3"], ["0.0.4"]),
        ("~0.1", ["0.1.0", "0.1.7"], ["0.2.0"]),
        ("~1", ["1.0.0", "1.9.0"], ["2.0.0"]),
        ("0.1.0", ["0.1.0"], ["0.1.1"]),
        ("0.1", ["0.1.4"], ["0.2.0"]),
        (">=0.21.0,<0.23.0", ["0.21.0", "0.22.9"], ["0.20.9", "0.23.0"]),
        ("~=1.4", ["1.4.0", "1.9.0"], ["2.0.0", "1.3.9"]),
        ("!=1.0.1", ["1.0.0"], ["1.0.1"]),
    ],
)
def test_a_range_admits_what_it_says(range_text: str, inside: list[str], outside: list[str]) -> None:
    parsed = Range.parse(range_text)
    assert all(parsed.admits(v) for v in inside)
    assert not any(parsed.admits(v) for v in outside)


def test_a_pre_release_satisfies_only_its_exact_version() -> None:
    assert not admits("^1", "1.1.0-rc.1")
    assert admits("1.1.0-rc.1", "1.1.0-rc.1")
    assert Version.parse("1.0.0-rc.1") < Version.parse("1.0.0") < Version.parse("1.0.1")
    assert Version.parse("1.0.0-2") < Version.parse("1.0.0-10") < Version.parse("1.0.0-a")


@pytest.mark.parametrize("bad", ["", "latest", ">=1,<x", "~=1"])
def test_an_unreadable_range_is_refused(bad: str) -> None:
    with pytest.raises(VersionError):
        Range.parse(bad)


def test_the_core_version_is_the_installed_distributions() -> None:
    from importlib import metadata

    assert core_version() == metadata.version("choregos-core")


# -- configuration -----------------------------------------------------------------------------------------

KEYLESS = {
    "identity": "https://github.com/acme/agents/.*",
    "issuer": "https://token.actions.githubusercontent.com",
}


def test_a_configuration_is_read_and_the_offline_directory_is_a_source(tmp_path: Path) -> None:
    config = parse_config(
        {
            "sources": [{"name": "acme", "index": "oci://ghcr.io/acme/index:1", "trust": KEYLESS}],
            "defaultPlugins": [{"source": "acme", "name": "hello", "version": "^1"}],
            "offline": "/ignored",
        },
        offline=str(tmp_path),
    )
    assert [s.name for s in config.all_sources()] == ["acme", "offline"]
    assert config.offline == tmp_path
    assert config.source("acme").trust.mode == "keyless"
    assert config.source("offline").trust.as_dict()["mode"] == "none"
    assert config.default_plugins[0].version == "^1"


@pytest.mark.parametrize(
    "source",
    [
        {"name": "acme", "index": "oci://ghcr.io/acme/index:1"},  # no trust
        {"name": "acme", "index": "oci://x/i:1", "trust": {"identity": "a"}},  # keyless without issuer
        {"name": "acme", "index": "oci://x/i:1", "trust": {"key": "k", **KEYLESS}},  # both
        {"name": "acme", "index": "http://x/index.yaml", "trust": KEYLESS},  # not https
        {"name": "offline", "index": "oci://x/i:1", "trust": KEYLESS},  # reserved
        {"name": "acme", "index": "https://u:p@x/index.yaml", "trust": KEYLESS},  # credentials
    ],
)
def test_a_source_without_a_usable_trust_or_index_is_refused(source: dict[str, Any]) -> None:
    with pytest.raises(SourceError) as caught:
        parse_config({"sources": [source]})
    assert _check(caught) == "config"


def test_a_default_plugin_names_a_declared_source() -> None:
    with pytest.raises(SourceError):
        parse_config({"defaultPlugins": [{"source": "nowhere", "name": "x", "version": "1"}]})


# -- indexes and resolution ------------------------------------------------------------------------------


def _index(**versions: dict[str, Any]) -> dict[str, Any]:
    return {"apiVersion": "choregos/v1", "kind": "PluginIndex", "plugins": {"hello": {"versions": versions}}}


def _oci(digest_: str, core: str) -> dict[str, Any]:
    return {"digest": digest_, "core": core, "oci": "ghcr.io/acme/hello:x"}


def test_the_newest_version_this_core_admits_is_chosen() -> None:
    index = parse_index(
        _index(
            **{"1.0.0": _oci(D1, ">=0.20.0"), "1.1.0": _oci(D2, ">=0.20.0"), "1.2.0": _oci(D3, ">=0.30.0")}
        )
    )
    assert resolve(index, "hello", "^1", "0.21.0").version == "1.1.0"
    assert resolve(index, "hello", "*", "0.31.0").version == "1.2.0"
    assert resolve(index, "hello", "1.0.0", "0.21.0").digest == D1
    entries = listing(index, "0.21.0")
    assert [v["version"] for v in entries[0]["versions"]] == ["1.2.0", "1.1.0", "1.0.0"]
    assert [v["admissible"] for v in entries[0]["versions"]] == [False, True, True]


def test_a_version_outside_the_core_range_is_refused() -> None:
    index = parse_index(_index(**{"1.2.0": _oci(D3, ">=0.30.0")}))
    with pytest.raises(SourceError) as caught:
        resolve(index, "hello", "1.2.0", "0.21.0")
    assert _check(caught) == "core"


def test_an_unknown_plugin_or_version_is_refused() -> None:
    index = parse_index(_index(**{"1.0.0": _oci(D1, ">=0.20.0")}))
    with pytest.raises(SourceError) as caught:
        resolve(index, "nope", "*", "0.21.0")
    assert _check(caught) == "not_found"
    with pytest.raises(SourceError) as caught:
        resolve(index, "hello", "^2", "0.21.0")
    assert _check(caught) == "version"


def test_a_git_release_not_pinned_to_a_40_hex_commit_is_refused() -> None:
    release = {
        "digest": D1,
        "core": ">=0.20.0",
        "git": {"url": "https://github.com/acme/a", "commit": "main"},
    }
    with pytest.raises(SourceError) as caught:
        parse_index(_index(**{"1.0.0": release}))
    assert _check(caught) == "index" and "commit" in caught.value.message


def test_an_index_that_is_not_one_is_refused() -> None:
    with pytest.raises(SourceError):
        parse_index(b"plugins: [")
    with pytest.raises(SourceError):
        parse_index(b"apiVersion: choregos/v1\nkind: Other\nplugins: {}\n")


# -- the offline directory -------------------------------------------------------------------------------


@pytest.fixture
def offline(tmp_path: Path) -> Path:
    directory = tmp_path / "offline"
    directory.mkdir()
    shutil.copytree(HELLO, directory / "hello")
    return directory


def test_the_offline_directory_is_an_index_of_directories_and_archives(offline: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    shutil.copytree(HELLO, other)
    manifest = yaml.safe_load((other / "plugin.yaml").read_text())
    manifest["metadata"]["version"] = "1.1.0"
    (other / "plugin.yaml").write_text(yaml.safe_dump(manifest))
    pack(other, offline / "hello-1.1.0.tar.gz")
    index = offline_index(offline)
    assert sorted(index.releases["hello"]) == ["1.0.0", "1.1.0"]
    assert index.trusted
    assert index.releases["hello"]["1.0.0"].digest == digest(HELLO)


def test_an_offline_plugin_whose_digest_differs_from_the_lock_is_refused(offline: Path) -> None:
    (offline / LOCK).write_text(yaml.safe_dump({"plugins": {"hello": {"version": "1.0.0", "digest": D1}}}))
    with pytest.raises(SourceError) as caught:
        offline_index(offline)
    assert _check(caught) == "lock"
    (offline / LOCK).write_text(
        yaml.safe_dump({"plugins": {"hello": {"version": "1.0.0", "digest": digest(HELLO)}}})
    )
    assert offline_index(offline).releases["hello"]["1.0.0"].digest == digest(HELLO)


def test_an_offline_plugin_absent_from_the_lock_is_refused(offline: Path) -> None:
    (offline / LOCK).write_text(yaml.safe_dump({"plugins": {}}))
    with pytest.raises(SourceError) as caught:
        offline_index(offline)
    assert _check(caught) == "lock"


# -- a git tarball -----------------------------------------------------------------------------------------


def _tarball(entries: list[tuple[str, bytes | None]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _hello_entries(prefix: str) -> list[tuple[str, bytes | None]]:
    return [
        (f"{prefix}/{p.relative_to(HELLO).as_posix()}", p.read_bytes())
        for p in HELLO.rglob("*")
        if p.is_file()
    ]


def test_a_git_tarball_yields_the_plugin_under_its_path(tmp_path: Path) -> None:
    tarball = _tarball(
        [("acme-agents-0123/README.md", b"x"), *_hello_entries("acme-agents-0123/plugins/hello")]
    )
    extract_git_path(tarball, "plugins/hello", tmp_path)
    assert digest(tmp_path) == digest(HELLO)


def test_a_link_in_a_git_tarball_refuses_the_plugin(tmp_path: Path) -> None:
    tarball = _tarball([*_hello_entries("top/hello"), ("top/hello/agents/evil.yaml", None)])
    with pytest.raises(SourceError) as caught:
        extract_git_path(tarball, "hello", tmp_path)
    assert _check(caught) == "content"


def test_an_index_says_which_agents_a_release_ships(offline: Path) -> None:
    """ADR 0108 §2: a catalogue names the plugin of a missing agent without fetching it."""
    index = parse_index(_index(**{"1.0.0": {**_oci(D1, ">=0.20.0"), "agents": ["greeter", "farewell"]}}))
    assert index.releases["hello"]["1.0.0"].agents == ("greeter", "farewell")
    assert parse_index(_index(**{"1.0.0": _oci(D1, ">=0.20.0")})).releases["hello"]["1.0.0"].agents == ()
    with pytest.raises(SourceError):
        parse_index(_index(**{"1.0.0": {**_oci(D1, ">=0.20.0"), "agents": ["Not A Slug"]}}))
    assert offline_index(offline).releases["hello"]["1.0.0"].agents == ("greeter",), (
        "read from the plugin itself"
    )
