# SPDX-License-Identifier: Apache-2.0
"""Plugin sources: read an index, fetch a plugin, verify it (ADR 0093 §3-4, S43-03).

`fetch_plugin(source, name, version)` is what an install or an upgrade calls (S43-04). It runs every
check of the supply-chain gate that does not need the database, in this order, and nothing is
handed to the caller unless all pass:

1. **resolve**: the newest version of the source's index inside the requested range whose `core`
   range admits the running core;
2. **signature**: an OCI release's manifest is verified by cosign against the source's trust policy
   (a keyless identity and issuer, or a public key), as hosted images are (ADR 0080 §8). A git
   release has no signature of its own: it is fetched only from an index that was itself verified
   (an `oci://` index), never from an unsigned `https://` index. The offline directory is trusted by
   its presence in the image, and checked against its `plugins.lock` when it has one;
3. **git ref**: a git release names a 40-hex commit; a branch or a tag is refused;
4. **digest**: the fetched files' content digest (the canonical tar's sha256) equals the index's;
5. **content**: every file is validated (`choregos_core.plugins.load_directory`), and the manifest's
   name, version and `core` agree with the index.

`assess_plugin` is the rest of the gate (ADR 0050): a plugin that brings a server needs a second
person's approval (the install answers 202, S43-04); its skills are assessed like any imported skill.

Egress (ADR 0066): every fetch goes through the guarded transport under a policy for the source.
Sources are declared by the operator, not by an organisation, so the policy opens the source's own
hosts (the index's host, the hosts its `egress` lists, a known registry's blob storage, and GitHub or
GitLab for a git release) and the deployment's ceiling and mode still bound them. An index that names
an artifact on another host reaches it only when the operator listed that host.

The network sits behind `PluginBackend`; `FakePluginSource` serves scripted indexes, artifacts,
tarballs and signatures to the tests and to `CHOREGOS_FAKES=1` (ADR 0005).
"""

from __future__ import annotations

import asyncio
import re
import tarfile
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
from choregos_adapters.base import ImageVerifier
from choregos_adapters.oci import (
    CosignVerifier,
    OciRegistryClient,
    RegistryError,
    UnconfiguredVerifier,
    parse_ref,
)
from choregos_adapters.oci.signatures import SignatureVerdict
from choregos_adapters.registry import fakes_enabled
from choregos_core.egress import AllowedRule, EgressPolicy, EgressRefusedError, normalize_host, use_policy
from choregos_core.plugins import Plugin, PluginError, digest, load_directory
from choregos_core.plugins import _safe_extract as extract_archive
from choregos_core.plugins.sources import (
    INDEX_MEDIA_TYPE,
    MAX_INDEX_BYTES,
    PLUGIN_MEDIA_TYPE,
    SHA,
    Index,
    PluginsConfig,
    Release,
    SourceConfig,
    SourceError,
    Trust,
    check_lock,
    extract_git_path,
    listing,
    load_config,
    offline_index,
    parse_index,
    read_lock,
    resolve,
)
from choregos_core.plugins.versions import admits, core_version

from ..config import get_settings
from ..logging import get_logger
from .skill_gate import Assessment, assess, raises_to_high

log = get_logger(__name__)

#: A compressed plugin larger than this is not downloaded further.
MAX_PLUGIN_BYTES = 64 * 1024 * 1024
TIMEOUT_S = 20.0
#: Where a registry redirects a blob download: reachable with the registry, not on their own.
BLOB_HOSTS: dict[str, tuple[str, ...]] = {
    "ghcr.io": ("pkg-containers.githubusercontent.com",),
    "docker.io": ("registry-1.docker.io", "auth.docker.io", "production.cloudflare.docker.com"),
    "registry-1.docker.io": ("auth.docker.io", "production.cloudflare.docker.com"),
}
#: What a git release's fetch calls (`skill_sources.HttpsSourceFetcher`).
GIT_HOSTS: dict[str, tuple[str, ...]] = {
    "github.com": ("api.github.com", "codeload.github.com"),
    "gitlab.com": ("gitlab.com",),
}


# ───────────────────────────── the network ─────────────────────────────


class PluginBackend(Protocol):
    async def index(self, url: str) -> bytes:
        """An `https://…/index.yaml`."""
        ...

    async def pull(self, reference: str, media_type: str, max_bytes: int) -> tuple[str, bytes]:
        """An OCI artifact's layer of `media_type`: `(manifest digest, bytes)`."""
        ...

    async def tarball(self, url: str, commit: str) -> bytes:
        """A git repository's tarball at a commit."""
        ...

    def verifier(self, trust: Trust) -> ImageVerifier:
        """What verifies a pinned artifact against `trust`."""
        ...


class HttpPluginBackend:
    """The real network: HTTPS for an index, the OCI distribution API for an artifact, GitHub or
    GitLab for a tarball, `cosign` for a signature. Every request goes through the guarded transport."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        #: the tests' `MockTransport`; the real one when `None`
        self.transport = transport

    async def index(self, url: str) -> bytes:
        from choregos_adapters.http.egress import guarded

        async with (
            httpx.AsyncClient(
                transport=guarded(self.transport), timeout=TIMEOUT_S, follow_redirects=False
            ) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code != 200:
                raise SourceError("fetch", f"{url} answered {response.status_code}")
            chunks: list[bytes] = []
            received = 0
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > MAX_INDEX_BYTES:
                    raise SourceError("index", "the index is larger than 1 MiB")
                chunks.append(chunk)
        return b"".join(chunks)

    async def pull(self, reference: str, media_type: str, max_bytes: int) -> tuple[str, bytes]:
        from choregos_adapters.http.egress import guarded

        client = OciRegistryClient(transport=guarded(self.transport), timeout_s=TIMEOUT_S)
        return await client.pull_artifact(reference, media_type, max_bytes)

    async def tarball(self, url: str, commit: str) -> bytes:
        from .skill_sources import HttpsSourceFetcher, SourceRefused, SourceUnavailable, parse_repo_url

        try:
            source = parse_repo_url(url)
            return await HttpsSourceFetcher(transport=self.transport).tarball(source, commit)
        except SourceRefused as exc:
            raise SourceError("content", str(exc)) from exc
        except SourceUnavailable as exc:
            raise SourceError("fetch", str(exc)) from exc

    def verifier(self, trust: Trust) -> ImageVerifier:
        try:
            return CosignVerifier(
                key=trust.key, identity=trust.identity, issuer=trust.issuer, tlog=trust.tlog
            )
        except ValueError as exc:  # a policy that names a signer wrongly verifies nothing
            return UnconfiguredVerifier(f"the source's trust policy is misconfigured: {exc}")


@dataclass(frozen=True)
class FakeSignature:
    identity: str = ""
    issuer: str = ""
    key: str = ""


class _FakeTrustVerifier:
    def __init__(self, source: FakePluginSource, trust: Trust) -> None:
        self.source = source
        self.trust = trust

    async def verify(self, pinned: str) -> SignatureVerdict:
        self.source.verified.append(pinned)
        signatures = self.source.signatures.get(pinned, [])
        if not signatures:
            return SignatureVerdict(False, "no signature")
        for sig in signatures:
            if self.trust.mode == "key" and sig.key and sig.key == self.trust.key:
                return SignatureVerdict(True, f"signed by the key {sig.key}")
            if (
                self.trust.mode == "keyless"
                and sig.identity
                and re.fullmatch(self.trust.identity, sig.identity)
                and re.fullmatch(self.trust.issuer, sig.issuer)
            ):
                return SignatureVerdict(True, f"signed by {sig.identity} ({sig.issuer})")
        return SignatureVerdict(False, "no signature is by the source's identity and issuer")


class FakePluginSource:
    """Scripted plugin sources (ADR 0005): indexes, OCI artifacts, git tarballs and signatures."""

    def __init__(self) -> None:
        self.indexes: dict[str, bytes] = {}
        self.artifacts: dict[str, tuple[str, str, bytes]] = {}
        self.tarballs: dict[tuple[str, str], bytes] = {}
        self.signatures: dict[str, list[FakeSignature]] = {}
        self.calls: list[tuple[str, str]] = []
        self.verified: list[str] = []

    # ───────── staging ─────────

    def publish_index(self, url: str, content: bytes | str) -> None:
        """An `https://` index."""
        self.indexes[url] = content.encode() if isinstance(content, str) else content

    def push(self, reference: str, content: bytes, media_type: str = PLUGIN_MEDIA_TYPE) -> str:
        """An OCI artifact under `reference`; returns the pinned reference cosign would verify."""
        import hashlib

        manifest = hashlib.sha256(media_type.encode() + content).hexdigest()
        pinned = parse_ref(reference).pinned(f"sha256:{manifest}")
        self.artifacts[reference] = (pinned, media_type, content)
        return pinned

    def sign(self, pinned: str, *, identity: str = "", issuer: str = "", key: str = "") -> None:
        self.signatures.setdefault(pinned, []).append(FakeSignature(identity, issuer, key))

    def add_tarball(self, url: str, commit: str, tarball: bytes) -> None:
        self.tarballs[(url, commit)] = tarball

    # ───────── PluginBackend ─────────

    async def index(self, url: str) -> bytes:
        self.calls.append(("index", url))
        if url not in self.indexes:
            raise SourceError("fetch", f"{url} answered 404")
        return self.indexes[url]

    async def pull(self, reference: str, media_type: str, max_bytes: int) -> tuple[str, bytes]:
        self.calls.append(("pull", reference))
        found = self.artifacts.get(reference)
        if found is None:
            raise RegistryError(f"`{reference}`: the registry has no such artifact")
        pinned, stored_type, content = found
        if stored_type != media_type:
            raise RegistryError(f"`{reference}`: the artifact holds no single `{media_type}` layer")
        if len(content) > max_bytes:
            raise RegistryError(f"`{reference}`: the artifact is larger than {max_bytes} bytes")
        return pinned.split("@", 1)[1], content

    async def tarball(self, url: str, commit: str) -> bytes:
        self.calls.append(("tarball", f"{url}@{commit}"))
        found = self.tarballs.get((url, commit))
        if found is None:
            raise SourceError("fetch", f"no tarball of {url} at {commit}")
        return found

    def verifier(self, trust: Trust) -> ImageVerifier:
        return _FakeTrustVerifier(self, trust)


_BACKEND: dict[str, PluginBackend] = {}


def set_backend(backend: PluginBackend | None) -> None:
    """Replaces the backend (the tests); `None` goes back to the default."""
    _BACKEND.clear()
    if backend is not None:
        _BACKEND["current"] = backend


def get_backend() -> PluginBackend:
    """The backend set, else `FakePluginSource` under `CHOREGOS_FAKES=1`, else the network."""
    if "current" not in _BACKEND:
        _BACKEND["current"] = FakePluginSource() if fakes_enabled() else HttpPluginBackend()
    return _BACKEND["current"]


def plugins_config() -> PluginsConfig:
    """`CHOREGOS_PLUGINS_CONFIG` and `CHOREGOS_PLUGINS_OFFLINE` read; `SourceError("config")` when invalid."""
    settings = get_settings()
    return load_config(settings.plugins_config or None, settings.plugins_offline or None)


# ───────────────────────────── egress ─────────────────────────────


def source_policy(source: SourceConfig, extra: tuple[str, ...] = ()) -> EgressPolicy:
    """What a source's fetches may reach: its own hosts, within the deployment's ceiling and mode."""
    from .egress import ceiling_of, mode_of

    settings = get_settings()
    hosts = {source.index_host, *source.egress, *extra}
    for host in list(hosts):
        hosts.update(BLOB_HOSTS.get(host, ()))
    rules = tuple(AllowedRule(normalize_host(h)) for h in sorted(hosts) if h)
    return EgressPolicy(
        org=f"plugin-source:{source.name}", rules=rules, ceiling=ceiling_of(settings), mode=mode_of(settings)
    )


def _fetch_error(exc: Exception) -> SourceError:
    if isinstance(exc, SourceError):
        return exc
    return SourceError("fetch", str(exc) or exc.__class__.__name__)


_NETWORK_ERRORS = (RegistryError, httpx.HTTPError, EgressRefusedError, OSError)


# ───────────────────────────── indexes ─────────────────────────────


async def read_index(
    source: SourceConfig, config: PluginsConfig, backend: PluginBackend | None = None
) -> Index:
    """A source's index: the offline directory read, an `oci://` index pulled and its signature
    verified, or an `https://` index downloaded (unsigned: its git releases are not fetched)."""
    if source.offline:
        assert config.offline is not None
        return await asyncio.to_thread(offline_index, config.offline)
    backend = backend or get_backend()
    try:
        with use_policy(source_policy(source), "api", f"plugin-source:{source.name}"):
            if source.index.startswith("oci://"):
                reference = source.index.removeprefix("oci://")
                manifest, content = await backend.pull(reference, INDEX_MEDIA_TYPE, MAX_INDEX_BYTES)
                pinned = parse_ref(reference).pinned(manifest)
                verdict = await backend.verifier(source.trust).verify(pinned)
                if not verdict.verified:
                    raise SourceError("signature", f"the index {pinned}: {verdict.detail}")
                return parse_index(content, trusted=True)
            return parse_index(await backend.index(source.index), trusted=False)
    except _NETWORK_ERRORS as exc:
        raise _fetch_error(exc) from exc


async def list_sources(
    config: PluginsConfig, core: str | None = None, backend: PluginBackend | None = None
) -> list[dict[str, Any]]:
    """Every source as `PluginSource`: its trust, and what its index offers, or why it cannot be read."""
    core = core or core_version()
    out = []
    for source in config.all_sources():
        entry: dict[str, Any] = {
            "name": source.name,
            "index": source.index,
            "trust": source.trust.as_dict(),
            "plugins": [],
            "error": None,
        }
        try:
            entry["plugins"] = listing(await read_index(source, config, backend), core)
        except SourceError as exc:
            entry["error"] = f"{exc.check}: {exc.message}"
            log.warning("plugin_source.unreadable", source=source.name, check=exc.check, detail=exc.message)
        out.append(entry)
    return out


# ───────────────────────────── the gate ─────────────────────────────


@dataclass(frozen=True)
class PluginGate:
    """What ADR 0050's gate says of a plugin's contents."""

    #: why a second person must approve; empty when one approval is enough
    reasons: tuple[str, ...] = ()
    #: each skill's assessment, by its path in the plugin; a `high` one needs the second approval
    skills: dict[str, Assessment] = field(default_factory=dict)

    @property
    def needs_second_approval(self) -> bool:
        return bool(self.reasons)


def assess_plugin(plugin: Plugin) -> PluginGate:
    """A second administrator approves a plugin that brings a server, or a skill the gate rates `high`
    (ADR 0050: what a single administrator may not let in alone). Every reason is listed."""
    reasons = [
        f"it brings the server `{name}` ({server.get('image', '')})"
        for name, server in sorted(plugin.servers.items())
    ]
    skills: dict[str, Assessment] = {}
    for ref in plugin.manifest["contents"].get("skills", []):
        directory = plugin.root / ref
        files = {
            p.relative_to(directory).as_posix(): p.read_bytes().decode("utf-8", errors="replace")
            for p in sorted(directory.rglob("*"))
            if p.is_file()
        }
        skills[ref] = assessment = assess(files)
        if assessment.risk == "high":
            why = sorted({f.code for f in assessment.findings if raises_to_high(f)})
            reasons.append(f"its skill `{ref}` is high risk ({', '.join(why)})")
    return PluginGate(tuple(reasons), skills)


# ───────────────────────────── fetching ─────────────────────────────


@dataclass(frozen=True)
class FetchedPlugin:
    """A plugin that passed the gate's checks; `plugin.root` lives as long as the context."""

    source: SourceConfig
    release: Release
    plugin: Plugin
    #: who signed it, or why it needs no signature (the offline directory, a signed index)
    signature: str
    gate: PluginGate


async def _fetch_oci(
    source: SourceConfig, release: Release, backend: PluginBackend, work: Path
) -> tuple[Path, str]:
    assert release.oci is not None
    manifest, blob = await backend.pull(release.oci, PLUGIN_MEDIA_TYPE, MAX_PLUGIN_BYTES)
    pinned = parse_ref(release.oci).pinned(manifest)
    verdict = await backend.verifier(source.trust).verify(pinned)
    if not verdict.verified:
        raise SourceError("signature", f"`{release.name}` {release.version} ({pinned}): {verdict.detail}")
    archive = work / "plugin.tar.gz"
    archive.write_bytes(blob)
    root = work / "plugin"
    root.mkdir()
    await asyncio.to_thread(_extract, archive, root)
    return root, verdict.detail


async def _fetch_git(
    source: SourceConfig, release: Release, index: Index, backend: PluginBackend, work: Path
) -> tuple[Path, str]:
    assert release.git is not None
    if not SHA.match(release.git.commit):
        raise SourceError(
            "git_ref", f"`{release.git.commit}` is not a 40-hex commit: a branch or a tag moves"
        )
    if not index.trusted:
        raise SourceError(
            "signature",
            f"`{release.name}` {release.version} is a git release of an unsigned index: "
            "a git release is trusted through a signed (oci://) index",
        )
    tarball = await backend.tarball(release.git.url, release.git.commit)
    root = work / "plugin"
    root.mkdir()
    await asyncio.to_thread(extract_git_path, tarball, release.git.path, root)
    return root, f"{release.git.url}@{release.git.commit}, named by the source's signed index"


async def _fetch_offline(config: PluginsConfig, release: Release, work: Path) -> tuple[Path, str]:
    assert release.path is not None and config.offline is not None
    lock = await asyncio.to_thread(read_lock, config.offline)
    if release.path.is_dir():
        root = release.path
    else:
        root = work / "plugin"
        root.mkdir()
        await asyncio.to_thread(_extract, release.path, root)
    check_lock(lock, release.name, release.version, await asyncio.to_thread(_content_digest, root))
    return root, "the deployment's offline directory" + (" (checked against plugins.lock)" if lock else "")


def _extract(archive: Path, root: Path) -> None:
    try:
        extract_archive(archive, root)
    except PluginError as exc:
        raise SourceError("content", str(exc)) from exc
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise SourceError(
            "content", f"the artifact is not a readable .tar.gz ({exc.__class__.__name__})"
        ) from exc


def _content_digest(root: Path) -> str:
    """The digest of what the manifest declares; a manifest that cannot say it refuses the content."""
    try:
        return digest(root)
    except PluginError as exc:
        raise SourceError("content", str(exc)) from exc


def _validated(root: Path, release: Release, core: str) -> Plugin:
    found = _content_digest(root)
    if found != release.digest:
        raise SourceError(
            "digest", f"`{release.name}` {release.version} is {found}; the index says {release.digest}"
        )
    try:
        plugin = load_directory(root)
    except PluginError as exc:
        raise SourceError("content", str(exc)) from exc
    if (plugin.name, plugin.version) != (release.name, release.version):
        raise SourceError(
            "content",
            f"the index's `{release.name}` {release.version} holds `{plugin.name}` {plugin.version}",
        )
    if not admits(plugin.core, core):
        raise SourceError(
            "core", f"`{plugin.name}` {plugin.version} requires core {plugin.core}; this core is {core}"
        )
    return plugin


@asynccontextmanager
async def fetch_plugin(
    source: str,
    name: str,
    version: str,
    *,
    config: PluginsConfig | None = None,
    core: str | None = None,
    backend: PluginBackend | None = None,
) -> AsyncIterator[FetchedPlugin]:
    """`name` from `source` at the newest version inside `version` this core admits, fetched and
    checked; `SourceError` names the check that refused. The plugin's files live as long as the context.

    ```python
    async with fetch_plugin("varga", "servicenow", "^0.1") as fetched:
        if fetched.gate.needs_second_approval: ...  # 202 (S43-04)
    ```
    """
    config = config or plugins_config()
    core = core or core_version()
    backend = backend or get_backend()
    declared = config.source(source)
    index = await read_index(declared, config, backend)
    release = resolve(index, name, version, core)
    with tempfile.TemporaryDirectory(prefix="choregos-plugin-fetch-") as tmp:
        work = Path(tmp)
        try:
            if declared.offline:
                root, signature = await _fetch_offline(config, release, work)
            elif release.oci is not None:
                with use_policy(source_policy(declared), "api", f"plugin-source:{declared.name}"):
                    root, signature = await _fetch_oci(declared, release, backend, work)
            else:
                assert release.git is not None
                host = (httpx.URL(release.git.url).host or "").lower()
                policy = source_policy(declared, GIT_HOSTS.get(host, ()))
                with use_policy(policy, "api", f"plugin-source:{declared.name}"):
                    root, signature = await _fetch_git(declared, release, index, backend, work)
        except _NETWORK_ERRORS as exc:
            raise _fetch_error(exc) from exc
        plugin = await asyncio.to_thread(_validated, root, release, core)
        log.info(
            "plugin_source.fetched",
            source=declared.name,
            plugin=name,
            version=release.version,
            digest=release.digest,
            signature=signature,
        )
        yield FetchedPlugin(declared, release, plugin, signature, assess_plugin(plugin))


__all__ = [
    "FakePluginSource",
    "FetchedPlugin",
    "HttpPluginBackend",
    "PluginBackend",
    "PluginGate",
    "SourceError",
    "assess_plugin",
    "fetch_plugin",
    "get_backend",
    "list_sources",
    "plugins_config",
    "read_index",
    "set_backend",
    "source_policy",
]
