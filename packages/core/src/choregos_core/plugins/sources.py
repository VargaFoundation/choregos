# SPDX-License-Identifier: Apache-2.0
"""Plugin sources (ADR 0093 §4, S43-03): the deployment's configuration, indexes, resolution, the
offline directory and its lock.

Nothing here touches the network: the API's `services/plugin_sources.py` fetches indexes and
artifacts and verifies signatures, then calls these pure functions. Every refusal is a
`SourceError` whose `check` names the guard that refused (`digest`, `signature`, `core`, …), so
an install can answer a 422 naming the check.

The configuration (`CHOREGOS_PLUGINS_CONFIG`, a YAML file the chart writes):

```yaml
sources:
  - name: varga
    index: oci://ghcr.io/vargafoundation/choregos-agents/index:stable   # or https://…/index.yaml
    trust:                       # a keyless identity and issuer (regular expressions, matched whole)…
      identity: https://github.com/VargaFoundation/choregos-agents/.*
      issuer: https://token.actions.githubusercontent.com
      # key: /etc/choregos/plugins/varga.pub   # …or a public key cosign reads
    egress: [pkg-containers.githubusercontent.com]   # hosts beyond the index's own
defaultPlugins:
  - {source: varga, name: servicenow, version: "^0.1"}
offline: /opt/choregos/plugins   # CHOREGOS_PLUGINS_OFFLINE wins over it
```

The offline directory is the source `offline`: every plugin directory or `.tar.gz` directly in it,
trusted by its presence in the image. When it holds a `plugins.lock`
(`plugins: {name: {version, digest}}`), every plugin must be in it with that digest.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from choregos_contracts import load_schema
from jsonschema import Draft202012Validator

from ..errors import ChoregosError
from . import MAX_BYTES, MAX_FILES, PluginError, opened
from .versions import Range, Version, VersionError

PLUGIN_MEDIA_TYPE = "application/vnd.choregos.plugin.v1.tar+gzip"
#: The layer media type of an index published as an OCI artifact (`oci://…` sources).
INDEX_MEDIA_TYPE = "application/vnd.choregos.plugin-index.v1+yaml"
OFFLINE = "offline"
LOCK = "plugins.lock"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
#: An index larger than this is not one.
MAX_INDEX_BYTES = 1024 * 1024

Check = Literal[
    "config",
    "index",
    "not_found",
    "version",
    "core",
    "digest",
    "signature",
    "git_ref",
    "lock",
    "content",
    "fetch",
]


class SourceError(ChoregosError):
    """A refusal by one guard; `check` names it."""

    def __init__(self, check: Check, message: str) -> None:
        super().__init__(message)
        self.check: Check = check
        self.message = message


# -- configuration ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Trust:
    """How a source's signatures are verified (`PluginTrust`)."""

    mode: Literal["keyless", "key", "none"]
    identity: str = ""
    issuer: str = ""
    key: str = ""
    tlog: bool = True

    def fingerprint(self) -> str | None:
        """sha256 of the public key (its file's bytes; a KMS URI's text)."""
        if not self.key:
            return None
        path = Path(self.key)
        data = path.read_bytes() if path.is_file() else self.key.encode()
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "issuer": self.issuer or None,
            "identity": self.identity or None,
            "key_fingerprint": self.fingerprint(),
        }


@dataclass(frozen=True, slots=True)
class SourceConfig:
    name: str
    index: str
    trust: Trust
    #: hosts the source's fetches may reach beyond the index's own (a registry's blob CDN, a mirror)
    egress: tuple[str, ...] = ()

    @property
    def offline(self) -> bool:
        return self.name == OFFLINE

    @property
    def index_host(self) -> str:
        if self.index.startswith("oci://"):
            return self.index.removeprefix("oci://").split("/", 1)[0]
        return (urlsplit(self.index).hostname or "").lower()


@dataclass(frozen=True, slots=True)
class DefaultPlugin:
    source: str
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class PluginsConfig:
    sources: tuple[SourceConfig, ...] = ()
    default_plugins: tuple[DefaultPlugin, ...] = ()
    offline: Path | None = None

    def all_sources(self) -> list[SourceConfig]:
        """The declared sources, then the offline directory as the source `offline`."""
        out = list(self.sources)
        if self.offline is not None:
            out.append(SourceConfig(OFFLINE, str(self.offline), Trust("none")))
        return out

    def source(self, name: str) -> SourceConfig:
        for source in self.all_sources():
            if source.name == name:
                return source
        raise SourceError("not_found", f"no plugin source `{name}` is declared")


def _trust(name: str, raw: Any) -> Trust:
    if not isinstance(raw, dict):
        raise SourceError("config", f"source `{name}`: `trust` names a public key, or an identity and issuer")
    key, identity, issuer = (str(raw.get(k) or "") for k in ("key", "identity", "issuer"))
    tlog = raw.get("tlog", True)
    if set(raw) - {"key", "identity", "issuer", "tlog"} or not isinstance(tlog, bool):
        raise SourceError("config", f"source `{name}`: `trust` holds key, identity, issuer and tlog only")
    if key and (identity or issuer):
        raise SourceError("config", f"source `{name}`: a public key, or a keyless identity, not both")
    if key:
        return Trust("key", key=key, tlog=tlog)
    if not (identity and issuer):
        raise SourceError("config", f"source `{name}`: a keyless trust needs both `identity` and `issuer`")
    for pattern in (identity, issuer):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SourceError("config", f"source `{name}`: `{pattern}` is not a regular expression") from exc
    return Trust("keyless", identity=identity, issuer=issuer, tlog=tlog)


def _source(raw: Any) -> SourceConfig:
    if not isinstance(raw, dict):
        raise SourceError("config", "a source is a mapping: name, index, trust")
    name = str(raw.get("name") or "")
    if not NAME.match(name) or name == OFFLINE:
        raise SourceError("config", f"`{name}` is not a source name (`offline` is the offline directory's)")
    index = str(raw.get("index") or "")
    if not index.startswith(("oci://", "https://")):
        raise SourceError("config", f"source `{name}`: the index is `oci://…` or `https://…`")
    if index.startswith("https://"):
        parts = urlsplit(index)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise SourceError("config", f"source `{name}`: the index URL carries no credentials or query")
    egress = raw.get("egress") or []
    if not isinstance(egress, list) or not all(isinstance(h, str) and h for h in egress):
        raise SourceError("config", f"source `{name}`: `egress` is a list of hosts")
    return SourceConfig(name, index, _trust(name, raw.get("trust")), tuple(h.lower() for h in egress))


def parse_config(doc: Any, offline: str | None = None) -> PluginsConfig:
    """The configuration a YAML document holds; `offline` (CHOREGOS_PLUGINS_OFFLINE) wins over its own."""
    doc = doc or {}
    if not isinstance(doc, dict) or set(doc) - {"sources", "defaultPlugins", "offline"}:
        raise SourceError("config", "the plugins configuration holds sources, defaultPlugins and offline")
    sources = tuple(_source(raw) for raw in doc.get("sources") or [])
    names = [s.name for s in sources]
    if len(set(names)) != len(names):
        raise SourceError("config", "two sources share a name")
    defaults: list[DefaultPlugin] = []
    for raw in doc.get("defaultPlugins") or []:
        if not isinstance(raw, dict) or not all(isinstance(raw.get(k), str) for k in ("source", "name")):
            raise SourceError("config", "a default plugin names its source, name and version")
        version = str(raw.get("version") or "*")
        if version != "*":
            try:
                Range.parse(version)
            except VersionError as exc:
                raise SourceError("config", f"default plugin `{raw['name']}`: {exc}") from exc
        if raw["source"] not in {*names, OFFLINE}:
            raise SourceError("config", f"default plugin `{raw['name']}`: no source `{raw['source']}`")
        defaults.append(DefaultPlugin(raw["source"], raw["name"], version))
    directory = offline or doc.get("offline") or None
    return PluginsConfig(sources, tuple(defaults), Path(str(directory)) if directory else None)


def load_config(path: str | None, offline: str | None = None) -> PluginsConfig:
    """`CHOREGOS_PLUGINS_CONFIG` read; no file is an empty configuration (plus the offline directory)."""
    if not path:
        return parse_config({}, offline)
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SourceError("config", f"{path}: the plugins configuration cannot be read ({exc})") from exc
    return parse_config(doc, offline)


# -- indexes ------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GitRelease:
    url: str
    commit: str
    path: str = ""


@dataclass(frozen=True, slots=True)
class Release:
    name: str
    version: str
    digest: str
    core: str
    description: str | None = None
    oci: str | None = None
    git: GitRelease | None = None
    #: the offline directory's plugin (a directory or a `.tar.gz`)
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class Index:
    releases: dict[str, dict[str, Release]] = field(default_factory=dict)
    #: whether the index itself was verified against the source's trust (an `oci://` index), or is
    #: the offline directory; a git release is fetched only from such an index
    trusted: bool = False


def parse_index(raw: bytes | str | dict[str, Any], *, trusted: bool = False) -> Index:
    """An `index.yaml` validated by `plugin-index.schema.json`."""
    if isinstance(raw, dict):
        doc: Any = raw
    else:
        if len(raw) > MAX_INDEX_BYTES:
            raise SourceError("index", "the index is larger than 1 MiB")
        try:
            doc = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise SourceError("index", f"the index is not YAML ({exc.__class__.__name__})") from exc
    errors = sorted(
        Draft202012Validator(load_schema("plugin-index.schema.json")).iter_errors(doc),
        key=lambda e: list(e.absolute_path),
    )
    if errors:
        first = errors[0]
        at = "/".join(str(p) for p in first.absolute_path)
        raise SourceError("index", f"the index is not a plugin index: {at or '(root)'}: {first.message}")
    releases: dict[str, dict[str, Release]] = {}
    for name, entry in doc["plugins"].items():
        for version, rel in entry["versions"].items():
            git = rel.get("git")
            releases.setdefault(name, {})[version] = Release(
                name=name,
                version=version,
                digest=rel["digest"],
                core=rel["core"],
                description=rel.get("description"),
                oci=rel.get("oci"),
                git=GitRelease(git["url"], git["commit"], git.get("path", "")) if git else None,
            )
    return Index(releases, trusted)


def _admits(range_text: str, version: str) -> bool:
    try:
        return Range.parse(range_text).admits(version)
    except VersionError:
        return False


def newest_first(versions: dict[str, Release]) -> list[Release]:
    readable = []
    for release in versions.values():
        try:
            readable.append((Version.parse(release.version), release))
        except VersionError:
            continue
    return [release for _, release in sorted(readable, key=lambda pair: pair[0], reverse=True)]


def resolve(index: Index, name: str, wanted: str, core: str) -> Release:
    """The newest release of `name` inside `wanted` (an exact version, a range, or `*`) whose `core`
    range admits the running core."""
    versions = index.releases.get(name)
    if not versions:
        raise SourceError("not_found", f"the source offers no plugin `{name}`")
    if wanted.strip() in {"", "*"}:
        matching = newest_first(versions)
    else:
        try:
            wanted_range = Range.parse(wanted)
        except VersionError as exc:
            raise SourceError("version", str(exc)) from exc
        matching = [r for r in newest_first(versions) if wanted_range.admits(r.version)]
    if not matching:
        offered = ", ".join(r.version for r in newest_first(versions))
        raise SourceError("version", f"no version of `{name}` matches `{wanted}` (offered: {offered})")
    for release in matching:
        if _admits(release.core, core):
            return release
    raise SourceError(
        "core",
        f"`{name}` {', '.join(r.version for r in matching)} require core "
        f"{', '.join(sorted({r.core for r in matching}))}; this core is {core}",
    )


def listing(index: Index, core: str) -> list[dict[str, Any]]:
    """The `PluginSourceEntry` list of an index: newest version first, each admissible or not."""
    out = []
    for name in sorted(index.releases):
        releases = newest_first(index.releases[name])
        out.append(
            {
                "name": name,
                "description": next((r.description for r in releases if r.description), None),
                "versions": [
                    {"version": r.version, "core": r.core, "admissible": _admits(r.core, core)}
                    for r in releases
                ],
            }
        )
    return out


# -- the offline directory -------------------------------------------------------------------------------


def read_lock(directory: Path) -> dict[str, tuple[str, str]] | None:
    """`plugins.lock` as name → (version, digest); `None` without a lock."""
    path = directory / LOCK
    if not path.is_file():
        return None
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SourceError("lock", f"{LOCK} cannot be read ({exc.__class__.__name__})") from exc
    plugins = doc.get("plugins") if isinstance(doc, dict) else None
    if not isinstance(plugins, dict):
        raise SourceError("lock", f"{LOCK} holds `plugins: {{name: {{version, digest}}}}`")
    lock: dict[str, tuple[str, str]] = {}
    for name, entry in plugins.items():
        if not isinstance(entry, dict) or not DIGEST.match(str(entry.get("digest", ""))):
            raise SourceError("lock", f"{LOCK}: `{name}` needs a version and a `sha256:` digest")
        lock[str(name)] = (str(entry.get("version", "")), str(entry["digest"]))
    return lock


def check_lock(lock: dict[str, tuple[str, str]] | None, name: str, version: str, digest: str) -> None:
    if lock is None:
        return
    if name not in lock:
        raise SourceError("lock", f"`{name}` is in the offline directory but not in {LOCK}")
    locked_version, locked_digest = lock[name]
    if locked_digest != digest or (locked_version and locked_version != version):
        raise SourceError(
            "lock", f"`{name}` {version} is {digest}; {LOCK} expects {locked_version} {locked_digest}"
        )


def _offline_candidates(directory: Path) -> list[Path]:
    return sorted(
        p
        for p in directory.iterdir()
        if (p.is_dir() and not p.is_symlink() and (p / "plugin.yaml").is_file())
        or (p.is_file() and p.name.endswith(".tar.gz"))
    )


def offline_index(directory: Path) -> Index:
    """The offline directory as an index: every plugin loaded, validated and checked against the lock."""
    if not directory.is_dir():
        raise SourceError("index", f"the offline directory {directory} does not exist")
    lock = read_lock(directory)
    releases: dict[str, dict[str, Release]] = {}
    for candidate in _offline_candidates(directory):
        try:
            with opened(candidate) as plugin:
                name, version, plugin_digest = plugin.name, plugin.version, plugin.digest
                core = plugin.core
                description = str(plugin.manifest["metadata"].get("description") or "") or None
        except PluginError as exc:
            raise SourceError("content", f"{candidate.name}: {exc}") from exc
        check_lock(lock, name, version, plugin_digest)
        if version in releases.get(name, {}):
            raise SourceError("index", f"`{name}` {version} is twice in the offline directory")
        releases.setdefault(name, {})[version] = Release(
            name, version, plugin_digest, core, description, path=candidate
        )
    return Index(releases, trusted=True)


# -- a git tarball -----------------------------------------------------------------------------------------


def extract_git_path(tarball: bytes, path: str, target: Path) -> None:
    """The files of a repository tarball under `path` (its top folder dropped) written into `target`.
    A link, a device or an entry that leaves the repository refuses the whole plugin."""
    base = posixpath.normpath(path.strip("/")) if path.strip("/") else ""
    if base.startswith(("..", "/")):
        raise SourceError("content", f"`{path}` goes outside the repository")
    try:
        archive = tarfile.open(fileobj=io.BytesIO(tarball), mode="r:*")  # noqa: SIM115 - closed below
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise SourceError("content", "the repository tarball cannot be read") from exc
    files = 0
    total = 0
    with archive:
        for info in archive:
            name = info.name.split("/", 1)[1] if "/" in info.name else ""
            if not name:
                continue
            normal = posixpath.normpath(name)
            if normal == ".." or normal.startswith(("../", "/")):
                raise SourceError("content", f"`{name}` goes outside the repository")
            if base and normal != base and not normal.startswith(base + "/"):
                continue
            relative = normal[len(base) + 1 :] if base else normal
            if info.isdir():
                continue
            if not info.isfile():
                raise SourceError("content", f"`{relative or normal}`: a link or a special file is refused")
            files += 1
            total += info.size
            if files > MAX_FILES or total > MAX_BYTES:
                raise SourceError("content", "the plugin is too large")
            data = archive.extractfile(info)
            if data is None or not relative:
                continue
            out = target / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data.read())
    if not (target / "plugin.yaml").is_file():
        raise SourceError("content", f"no plugin.yaml under `{path or '/'}` at that commit")


__all__ = [
    "DIGEST",
    "INDEX_MEDIA_TYPE",
    "LOCK",
    "OFFLINE",
    "PLUGIN_MEDIA_TYPE",
    "SHA",
    "DefaultPlugin",
    "GitRelease",
    "Index",
    "PluginsConfig",
    "Release",
    "SourceConfig",
    "SourceError",
    "Trust",
    "check_lock",
    "extract_git_path",
    "listing",
    "load_config",
    "newest_first",
    "offline_index",
    "parse_config",
    "parse_index",
    "read_lock",
    "resolve",
]
