# SPDX-License-Identifier: Apache-2.0
"""Plugin sources and trust (ADR 0093 §4, S43-03): `fetch_plugin` hands over a plugin only when
every check passed, and each guard refuses its own case. `FakePluginSource` serves the indexes,
artifacts, tarballs and signatures; one test drives the real backend through a `MockTransport`."""

from __future__ import annotations

import io
import shutil
import tarfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
import yaml
from choregos_api.services.plugin_sources import (
    FakePluginSource,
    HttpPluginBackend,
    _fetch_git,
    _fetch_offline,
    assess_plugin,
    fetch_plugin,
    list_sources,
    read_index,
    set_backend,
)
from choregos_contracts import load_openapi
from choregos_core.plugins import digest, load_directory, pack
from choregos_core.plugins.sources import (
    INDEX_MEDIA_TYPE,
    LOCK,
    GitRelease,
    Index,
    PluginsConfig,
    Release,
    SourceError,
    parse_config,
)
from httpx import AsyncClient

from .conftest import login

HELLO = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "hello"
CORE = "0.21.0"
IDENTITY = "https://github.com/acme/agents/.github/workflows/release.yml@refs/heads/main"
ISSUER = "https://token.actions.githubusercontent.com"
KEYLESS = {"identity": "https://github.com/acme/agents/.*", "issuer": ISSUER}
COMMIT = "0123456789abcdef0123456789abcdef01234567"
HELLO_DIGEST = digest(HELLO)


@pytest.fixture
def fake() -> Iterator[FakePluginSource]:
    backend = FakePluginSource()
    set_backend(backend)
    yield backend
    set_backend(None)


def _archive(root: Path, tmp_path: Path) -> bytes:
    out = tmp_path / f"{root.name}.tar.gz"
    pack(root, out)
    return out.read_bytes()


def _index(release: dict[str, Any], version: str = "1.0.0") -> str:
    return yaml.safe_dump(
        {
            "apiVersion": "choregos/v1",
            "kind": "PluginIndex",
            "plugins": {"hello": {"versions": {version: release}}},
        }
    )


def _config(index: str, trust: dict[str, Any] | None = None, **extra: Any) -> PluginsConfig:
    return parse_config({"sources": [{"name": "acme", "index": index, "trust": trust or KEYLESS, **extra}]})


def _oci_source(
    fake: FakePluginSource, tmp_path: Path, *, digest_: str = HELLO_DIGEST, core: str = ">=0.20.0"
) -> str:
    """An https index naming an OCI release of hello; returns the release's pinned reference."""
    pinned = fake.push("ghcr.io/acme/hello:1.0.0", _archive(HELLO, tmp_path))
    fake.publish_index(
        "https://acme.example/index.yaml",
        _index({"digest": digest_, "core": core, "oci": "ghcr.io/acme/hello:1.0.0"}),
    )
    return pinned


async def _refused(config: PluginsConfig, version: str = "^1") -> SourceError:
    with pytest.raises(SourceError) as caught:
        async with fetch_plugin("acme", "hello", version, config=config, core=CORE):
            pass
    return caught.value


# -- OCI releases and their signature --------------------------------------------------------------------


async def test_a_signed_oci_plugin_is_fetched_and_validated(fake: FakePluginSource, tmp_path: Path) -> None:
    pinned = _oci_source(fake, tmp_path)
    fake.sign(pinned, identity=IDENTITY, issuer=ISSUER)
    config = _config("https://acme.example/index.yaml")
    async with fetch_plugin("acme", "hello", "^1", config=config, core=CORE) as fetched:
        assert fetched.plugin.name == "hello" and fetched.plugin.digest == HELLO_DIGEST
        assert fetched.release.version == "1.0.0"
        assert IDENTITY in fetched.signature
        assert (fetched.plugin.root / "plugin.yaml").is_file()
        root = fetched.plugin.root
    assert not root.exists()  # the files live as long as the context
    assert fake.verified == [pinned]


async def test_an_unsigned_plugin_is_refused(fake: FakePluginSource, tmp_path: Path) -> None:
    _oci_source(fake, tmp_path)
    refusal = await _refused(_config("https://acme.example/index.yaml"))
    assert refusal.check == "signature"


async def test_a_plugin_signed_by_another_identity_is_refused(fake: FakePluginSource, tmp_path: Path) -> None:
    pinned = _oci_source(fake, tmp_path)
    fake.sign(
        pinned,
        identity="https://github.com/evil/agents/.github/workflows/x.yml@refs/heads/main",
        issuer=ISSUER,
    )
    fake.sign(pinned, identity=IDENTITY, issuer="https://evil.example")
    refusal = await _refused(_config("https://acme.example/index.yaml"))
    assert refusal.check == "signature" and "identity" in refusal.message


async def test_a_public_key_trust_accepts_its_key_only(fake: FakePluginSource, tmp_path: Path) -> None:
    pinned = _oci_source(fake, tmp_path)
    fake.sign(pinned, key="/keys/other.pub")
    config = _config("https://acme.example/index.yaml", {"key": "/keys/acme.pub"})
    assert (await _refused(config)).check == "signature"
    fake.sign(pinned, key="/keys/acme.pub")
    async with fetch_plugin("acme", "hello", "1.0.0", config=config, core=CORE) as fetched:
        assert "acme.pub" in fetched.signature


async def test_a_digest_that_differs_from_the_index_is_refused(
    fake: FakePluginSource, tmp_path: Path
) -> None:
    pinned = _oci_source(fake, tmp_path, digest_="sha256:" + "0" * 64)
    fake.sign(pinned, identity=IDENTITY, issuer=ISSUER)
    refusal = await _refused(_config("https://acme.example/index.yaml"))
    assert refusal.check == "digest" and HELLO_DIGEST in refusal.message


async def test_a_version_outside_the_core_range_is_refused(fake: FakePluginSource, tmp_path: Path) -> None:
    pinned = _oci_source(fake, tmp_path, core=">=0.30.0")
    fake.sign(pinned, identity=IDENTITY, issuer=ISSUER)
    refusal = await _refused(_config("https://acme.example/index.yaml"))
    assert refusal.check == "core"
    assert pinned not in fake.verified  # refused before anything was fetched


async def test_an_index_that_lies_about_the_manifests_core_is_refused(
    fake: FakePluginSource, tmp_path: Path
) -> None:
    copy = tmp_path / "hello"
    shutil.copytree(HELLO, copy)
    manifest = yaml.safe_load((copy / "plugin.yaml").read_text())
    manifest["metadata"]["core"] = ">=0.30.0"
    (copy / "plugin.yaml").write_text(yaml.safe_dump(manifest))
    pinned = fake.push("ghcr.io/acme/hello:1.0.0", _archive(copy, tmp_path))
    fake.sign(pinned, identity=IDENTITY, issuer=ISSUER)
    fake.publish_index(
        "https://acme.example/index.yaml",
        _index({"digest": digest(copy), "core": ">=0.20.0", "oci": "ghcr.io/acme/hello:1.0.0"}),
    )
    assert (await _refused(_config("https://acme.example/index.yaml"))).check == "core"


# -- an oci:// index, git releases ----------------------------------------------------------------------


def _git_tarball(prefix: str = "acme-agents-0123456/plugins/hello") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(HELLO.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                info = tarfile.TarInfo(f"{prefix}/{path.relative_to(HELLO).as_posix()}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


GIT_RELEASE = {
    "digest": HELLO_DIGEST,
    "core": ">=0.20.0",
    "git": {"url": "https://github.com/acme/agents", "commit": COMMIT, "path": "plugins/hello"},
}


async def test_a_git_release_of_a_signed_oci_index_is_fetched(fake: FakePluginSource) -> None:
    pinned = fake.push("ghcr.io/acme/index:stable", _index(GIT_RELEASE).encode(), INDEX_MEDIA_TYPE)
    fake.sign(pinned, identity=IDENTITY, issuer=ISSUER)
    fake.add_tarball("https://github.com/acme/agents", COMMIT, _git_tarball())
    config = _config("oci://ghcr.io/acme/index:stable")
    async with fetch_plugin("acme", "hello", "1.0.0", config=config, core=CORE) as fetched:
        assert fetched.plugin.digest == HELLO_DIGEST
        assert COMMIT in fetched.signature


async def test_an_unsigned_oci_index_is_refused(fake: FakePluginSource) -> None:
    fake.push("ghcr.io/acme/index:stable", _index(GIT_RELEASE).encode(), INDEX_MEDIA_TYPE)
    refusal = await _refused(_config("oci://ghcr.io/acme/index:stable"))
    assert refusal.check == "signature" and "index" in refusal.message


async def test_a_git_release_of_an_unsigned_https_index_is_refused(fake: FakePluginSource) -> None:
    fake.publish_index("https://acme.example/index.yaml", _index(GIT_RELEASE))
    fake.add_tarball("https://github.com/acme/agents", COMMIT, _git_tarball())
    refusal = await _refused(_config("https://acme.example/index.yaml"))
    assert refusal.check == "signature" and "unsigned" in refusal.message
    assert not any(call[0] == "tarball" for call in fake.calls)


async def test_a_git_ref_that_is_not_a_40_hex_commit_is_refused(
    fake: FakePluginSource, tmp_path: Path
) -> None:
    moving = {**GIT_RELEASE, "git": {**GIT_RELEASE["git"], "commit": "main"}}  # type: ignore[dict-item]
    fake.publish_index("https://acme.example/index.yaml", _index(moving))
    with pytest.raises(SourceError) as caught:
        await read_index(_config("https://acme.example/index.yaml").source("acme"), PluginsConfig(), fake)
    assert caught.value.check == "index" and "commit" in caught.value.message
    # The fetch checks it again, whatever built the release.
    release = Release(
        "hello", "1.0.0", HELLO_DIGEST, ">=0.20.0", git=GitRelease("https://github.com/a/b", "v1.0.0")
    )
    source = _config("https://acme.example/index.yaml").source("acme")
    with pytest.raises(SourceError) as caught:
        await _fetch_git(source, release, Index(trusted=True), fake, tmp_path)
    assert caught.value.check == "git_ref"


# -- the offline directory -----------------------------------------------------------------------------


@pytest.fixture
def offline(tmp_path: Path) -> Path:
    directory = tmp_path / "offline"
    directory.mkdir()
    pack(HELLO, directory / "hello-1.0.0.tar.gz")
    return directory


async def test_an_offline_plugin_is_fetched_without_a_signature(
    fake: FakePluginSource, offline: Path
) -> None:
    config = parse_config({}, offline=str(offline))
    async with fetch_plugin("offline", "hello", "^1", config=config, core=CORE) as fetched:
        assert fetched.plugin.digest == HELLO_DIGEST
        assert "offline directory" in fetched.signature
    assert fake.calls == []


async def test_an_offline_plugin_that_differs_from_the_lock_is_refused(
    fake: FakePluginSource, offline: Path
) -> None:
    (offline / LOCK).write_text(
        yaml.safe_dump({"plugins": {"hello": {"version": "1.0.0", "digest": "sha256:" + "9" * 64}}})
    )
    with pytest.raises(SourceError) as caught:
        async with fetch_plugin(
            "offline", "hello", "^1", config=parse_config({}, offline=str(offline)), core=CORE
        ):
            pass
    assert caught.value.check == "lock"


async def test_the_fetch_checks_the_lock_again(offline: Path, tmp_path: Path) -> None:
    """The directory may change between reading its index and fetching: the fetch digests again."""
    config = parse_config({}, offline=str(offline))
    release = Release("hello", "1.0.0", HELLO_DIGEST, ">=0.20.0", path=offline / "hello-1.0.0.tar.gz")
    (offline / LOCK).write_text(
        yaml.safe_dump({"plugins": {"hello": {"version": "1.0.0", "digest": "sha256:" + "9" * 64}}})
    )
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SourceError) as caught:
        await _fetch_offline(config, release, work)
    assert caught.value.check == "lock"


# -- the supply-chain gate -------------------------------------------------------------------------------


def test_a_plugin_that_brings_a_server_needs_a_second_approval(tmp_path: Path) -> None:
    gate = assess_plugin(load_directory(HELLO))
    assert gate.needs_second_approval
    assert "hello" in gate.reasons[0]
    assert set(gate.skills) == {"skills/greeting"}

    copy = tmp_path / "hello"
    shutil.copytree(HELLO, copy)
    manifest = yaml.safe_load((copy / "plugin.yaml").read_text())
    manifest["contents"]["servers"] = []
    (copy / "plugin.yaml").write_text(yaml.safe_dump(manifest))
    shutil.rmtree(copy / "servers")
    assert not assess_plugin(load_directory(copy)).needs_second_approval


# -- egress, through the real backend -------------------------------------------------------------------


async def test_fetches_go_through_the_egress_guard(
    monkeypatch: pytest.MonkeyPatch, app: Any, tmp_path: Path
) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()
    seen: list[str] = []
    index = _index(
        {"digest": HELLO_DIGEST, "core": ">=0.20.0", "oci": "registry.evil.example/acme/hello:1.0.0"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, content=index.encode())

    backend = HttpPluginBackend(transport=httpx.MockTransport(handler))
    config = _config("https://acme.example/index.yaml")
    listed = await list_sources(config, CORE, backend)
    assert listed[0]["error"] is None and listed[0]["plugins"][0]["name"] == "hello"
    with pytest.raises(SourceError) as caught:
        async with fetch_plugin("acme", "hello", "1.0.0", config=config, core=CORE, backend=backend):
            pass
    assert caught.value.check == "fetch" and "registry.evil.example" in caught.value.message
    assert seen == ["acme.example", "acme.example"]  # the undeclared registry was never reached


# -- the route ------------------------------------------------------------------------------------------


def _schema() -> dict[str, Any]:
    return {"$ref": "#/components/schemas/PluginSources", "components": load_openapi()["components"]}


async def test_a_member_lists_the_sources(
    client: AsyncClient,
    admin: str,
    fake: FakePluginSource,
    offline: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api.config import reset_settings_cache

    fake.publish_index(
        "https://acme.example/index.yaml",
        _index({"digest": HELLO_DIGEST, "core": ">=99.0.0", "oci": "ghcr.io/acme/hello:1"}),
    )
    path = tmp_path / "plugins.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {"name": "acme", "index": "https://acme.example/index.yaml", "trust": KEYLESS},
                    {
                        "name": "broken",
                        "index": "https://down.example/index.yaml",
                        "trust": {"key": "/k.pub"},
                    },
                ]
            }
        )
    )
    monkeypatch.setenv("CHOREGOS_PLUGINS_CONFIG", str(path))
    monkeypatch.setenv("CHOREGOS_PLUGINS_OFFLINE", str(offline))
    reset_settings_cache()
    response = await client.get("/api/v1/orgs/varga/plugin-sources")
    assert response.status_code == 200, response.text
    body = response.json()
    jsonschema.validate(body, _schema())
    by_name = {s["name"]: s for s in body["sources"]}
    assert set(by_name) == {"acme", "broken", "offline"}
    assert by_name["acme"]["trust"] == {
        "mode": "keyless",
        "issuer": ISSUER,
        "identity": KEYLESS["identity"],
        "key_fingerprint": None,
    }
    assert by_name["acme"]["plugins"][0]["versions"][0]["admissible"] is False
    assert by_name["broken"]["error"] and by_name["broken"]["plugins"] == []
    assert by_name["broken"]["trust"]["key_fingerprint"].startswith("sha256:")
    assert by_name["offline"]["trust"]["mode"] == "none"
    assert by_name["offline"]["plugins"][0]["versions"][0]["admissible"] is True


async def test_a_stranger_cannot_list_the_sources(client: AsyncClient, org: str) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    await login(client, "dev@varga.dev")  # a member of varga only
    async with session_scope() as session:
        session.add(Organization(slug="other", name="Other"))
    assert (await client.get("/api/v1/orgs/varga/plugin-sources")).status_code == 200
    response = await client.get("/api/v1/orgs/other/plugin-sources")
    assert response.status_code == 403


async def test_an_invalid_configuration_is_reported(
    client: AsyncClient, admin: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    path = tmp_path / "plugins.yaml"
    path.write_text(yaml.safe_dump({"sources": [{"name": "acme", "index": "oci://x/i:1"}]}))
    monkeypatch.setenv("CHOREGOS_PLUGINS_CONFIG", str(path))
    reset_settings_cache()
    response = await client.get("/api/v1/orgs/varga/plugin-sources")
    assert response.status_code == 500 and "trust" in response.json()["detail"]
