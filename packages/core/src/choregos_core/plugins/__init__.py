# SPDX-License-Identifier: Apache-2.0
"""Plugins (ADR 0093): read a plugin directory or archive, validate everything it ships, digest it.

A plugin is a directory with `plugin.yaml` at its root (`plugin.schema.json`). **A plugin is what its
manifest declares**: `plugin.yaml`, a `README.md` and a `LICENSE` at the root when present, and exactly
the paths of `contents` (a file, every file of a skill or template directory, every file a procedure
pattern matches). Anything else in the directory — a server's source, a `.venv`, build output — is
neither read, validated, digested nor packed (S43-05b). Loading checks, and reports every problem found
rather than the first:

- the manifest against `plugin.schema.json`;
- no symbolic link on a declared path or under a declared directory, no path that leaves the plugin
  (the template rules, `choregos_core.paths`);
- each agent file in the catalogue entry shape (`catalogue:` + `agent:`, ADR 0040). Only its structure
  is checked here: the full `AgentCreate` model lives in the API, which the installer (S43-04) applies;
- each skill directory holds a `SKILL.md`;
- each template's `manifest.yaml` against `template.schema.json`;
- each eval suite against `plugin-evals.schema.json`, naming an agent of the plugin;
- each procedure through the ADR 0067 parser (`choregos_core.procedures.parse`), and each
  `{{plugin_webhook_url:<plugin>/<server>}}` it holds names this plugin and a server of it with a webhook;
- each template that takes its tracker from a plugin (`requires.connectors.tracker:
  plugin:<plugin>/<server>`) names this plugin and a server of it that `implements: [tracker]`;
- each server file against `plugin-server.schema.json`, plus what a schema cannot say (unique
  operation names, a compensation that names an operation of the same server, egress fields that the
  configuration schema declares, and — for a server that `implements: [tracker]` — every tool of the
  tracker profile declared with its access, `TRACKER_PROFILE`).

The plugin's identity is its **content digest**: the sha256 of its canonical tar (its declared files
only, entries sorted by path, mode 0644, modification time 0, owner and group 0). `pack` writes that tar
gzip-compressed, itself reproducible (gzip header time 0, no file name).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from choregos_contracts import load_schema
from jsonschema import Draft202012Validator

from ..errors import ChoregosError
from ..paths import PathOutsideError, files_under, inside, symlinks_under

MANIFEST = "plugin.yaml"
#: Files at the root that ship with a plugin when present, though `contents` does not name them.
ROOT_FILES = ("README.md", "LICENSE")
#: `requires.connectors.tracker: plugin:<plugin>/<server>` in a template: a plugin's server is the tracker.
PLUGIN_TRACKER = "plugin:"
#: Bounds on what a plugin may hold, unpacked: generous for agents, skills and templates, small
#: enough that an archive cannot fill a disk.
#: The tracker profile (plan S43 Decision 3, `plugin-tracker-profile.schema.json`): the tools a server
#: that `implements: [tracker]` serves, and the access each must be declared with. A write declared as a
#: read would be allowed to every agent without approval, so the access is part of the profile.
TRACKER_PROFILE: dict[str, str] = {
    "tracker_fetch_item": "read",
    "tracker_create_item": "write",
    "tracker_set_state": "write",
    "tracker_comment": "write",
    "tracker_upsert_status": "write",
    "tracker_close": "write",
    "tracker_list_candidates": "read",
    "tracker_parse_event": "read",
}
MAX_FILES = 2000
MAX_BYTES = 50 * 1024 * 1024
AGENT_SLUG = r"^[a-z][a-z0-9-]{1,62}$"


class PluginError(ChoregosError):
    """A plugin that cannot be loaded; `problems` lists every reason, each with its file."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("invalid plugin:\n" + "\n".join(f"  - {p}" for p in problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Plugin:
    """A validated plugin directory."""

    root: Path
    manifest: dict[str, Any]
    digest: str
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    procedures: tuple[str, ...] = ()
    #: the declared files, relative POSIX paths, sorted: what the digest covers and `pack` writes
    files: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return str(self.manifest["metadata"]["name"])

    @property
    def version(self) -> str:
        return str(self.manifest["metadata"]["version"])

    @property
    def core(self) -> str:
        return str(self.manifest["metadata"]["core"])


def _validator(schema: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(schema))


def _schema_problems(where: str, schema: str, document: Any) -> list[str]:
    out = []
    for error in sorted(_validator(schema).iter_errors(document), key=lambda e: list(e.absolute_path)):
        at = "/".join(str(p) for p in error.absolute_path)
        out.append(f"{where}{':' + at if at else ''}: {error.message}")
    return out


def _yaml(path: Path, where: str, problems: list[str]) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        problems.append(f"{where}: not readable YAML ({exc.__class__.__name__})")
        return None


def _target(root: Path, ref: str, problems: list[str]) -> Path | None:
    try:
        return inside(root, ref)
    except PathOutsideError:
        problems.append(f"{ref}: goes outside the plugin's folder")
        return None


# -- contents ---------------------------------------------------------------------------------------


def _check_agent(path: Path, ref: str, problems: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        problems.append(f"{ref}: agent file missing")
        return None
    doc = _yaml(path, ref, problems)
    if (
        not isinstance(doc, dict)
        or not isinstance(doc.get("catalogue"), dict)
        or not isinstance(doc.get("agent"), dict)
    ):
        problems.append(f"{ref}: an agent file holds `catalogue:` and `agent:` mappings (ADR 0040)")
        return None
    meta, agent = doc["catalogue"], doc["agent"]
    version = meta.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        problems.append(f"{ref}: catalogue.version is a positive integer")
    for key in ("role", "summary"):
        if not isinstance(meta.get(key), str) or not meta[key].strip():
            problems.append(f"{ref}: catalogue.{key} is required")
    slug = agent.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(AGENT_SLUG, slug):
        problems.append(f"{ref}: agent.slug must match {AGENT_SLUG}")
    if not isinstance(agent.get("display_name"), str) or not agent["display_name"].strip():
        problems.append(f"{ref}: agent.display_name is required")
    if "spec" in agent and not isinstance(agent["spec"], dict):
        problems.append(f"{ref}: agent.spec is a mapping")
    if agent.get("kind", "internal") not in {"internal", "external", "remote"}:
        problems.append(f"{ref}: agent.kind is internal, external or remote")
    return doc


def _check_server(path: Path, ref: str, problems: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        problems.append(f"{ref}: server file missing")
        return None
    doc = _yaml(path, ref, problems)
    found = _schema_problems(ref, "plugin-server.schema.json", doc)
    if found:
        problems.extend(found)
        return None
    assert isinstance(doc, dict)
    names = [str(op["name"]) for op in doc["operations"]]
    for duplicate in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"{ref}: operation `{duplicate}` is declared twice")
    for op in doc["operations"]:
        undo = (op.get("compensation") or {}).get("operation")  # ADR 0096: `{operation, arguments}`
        if undo is not None and undo not in names:
            problems.append(
                f"{ref}: `{op['name']}` is compensated by `{undo}`, which the server does not declare"
            )
    if "tracker" in (doc.get("implements") or []):
        access = {str(op["name"]): str(op["access"]) for op in doc["operations"]}
        for tool, expected in TRACKER_PROFILE.items():
            if tool not in access:
                problems.append(
                    f"{ref}: implements `tracker` but does not declare `{tool}` (the tracker profile)"
                )
            elif access[tool] != expected:
                problems.append(
                    f"{ref}: `{tool}` is a {expected} of the tracker profile, declared `{access[tool]}`"
                )
    declared = set((doc.get("config_schema") or {}).get("properties") or {})
    for name in (doc.get("egress") or {}).get("from_fields") or []:
        if name not in declared:
            problems.append(f"{ref}: egress field `{name}` is not in config_schema")
    return doc


def _check_eval(path: Path, ref: str, agents: set[str], problems: list[str]) -> None:
    if not path.is_file():
        problems.append(f"{ref}: eval file missing")
        return
    doc = _yaml(path, ref, problems)
    found = _schema_problems(ref, "plugin-evals.schema.json", doc)
    if found:
        problems.extend(found)
        return
    assert isinstance(doc, dict)
    if doc["agent"] not in agents:
        problems.append(f"{ref}: evaluates `{doc['agent']}`, which the plugin does not ship")
    names = [str(case["name"]) for case in doc.get("cases") or []]
    for duplicate in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"{ref}: case `{duplicate}` is declared twice")


def _check_procedure(
    path: Path, ref: str, plugin: str, servers: dict[str, dict[str, Any]], problems: list[str]
) -> str | None:
    from ..procedures import ProcedureError, parse, plugin_webhooks_in

    try:
        proc = parse(path.read_text(encoding="utf-8"), ref, origin=f"plugin:{plugin}")
    except (ProcedureError, UnicodeDecodeError) as exc:
        problems.append(str(exc) if isinstance(exc, ProcedureError) else f"{ref}: not UTF-8 text")
        return None
    for owner, server in plugin_webhooks_in(proc):
        if owner != plugin:
            problems.append(
                f"{ref}: `plugin_webhook_url:{owner}/{server}` names another plugin than `{plugin}`"
            )
        elif "webhook" not in servers.get(server, {}):
            problems.append(
                f"{ref}: `plugin_webhook_url:{owner}/{server}` names no server of `{plugin}` with a webhook"
            )
    return proc.id


def _procedure_files(root: Path, pattern: str, problems: list[str]) -> list[tuple[str, Path]]:
    if "*" not in pattern:
        target = _target(root, pattern, problems)
        return [(pattern, target)] if target is not None else []
    matches = sorted(p for p in root.glob(pattern) if p.is_file())
    if not matches:
        problems.append(f"{pattern}: matches no file")
    out = []
    for path in matches:
        ref = path.relative_to(root).as_posix()
        target = _target(root, ref, problems)
        if target is not None:
            out.append((ref, target))
    return out


# -- what a plugin is made of ---------------------------------------------------------------------------


def _link_on(root: Path, ref: str) -> str | None:
    """The first prefix of `ref` that is a symbolic link (a link to a directory included), else None."""
    current = root
    for part in PurePosixPath(ref).parts:
        current = current / part
        if current.is_symlink():
            return current.relative_to(root).as_posix()
    return None


def _declared_refs(root: Path, contents: dict[str, list[str]]) -> list[str]:
    refs = [MANIFEST, *(n for n in ROOT_FILES if (root / n).is_symlink() or (root / n).is_file())]
    for kind in ("agents", "evals", "servers", "skills", "templates"):
        refs.extend(contents.get(kind, []))
    for pattern in contents.get("procedures", []):
        if "*" in pattern:
            refs.extend(sorted(p.relative_to(root).as_posix() for p in root.glob(pattern)))
        else:
            refs.append(pattern)
    return list(dict.fromkeys(refs))


def _declared(root: Path, contents: dict[str, list[str]]) -> tuple[dict[str, Path], list[str]]:
    """The files the manifest declares, relative POSIX path → path, and why some are refused.

    A declared path that is missing is not a problem here: the content checks say so in their own
    words. A link on a declared path, or under a declared directory, is refused: nothing is read
    through it. Nothing else in the directory is looked at."""
    problems: list[str] = []
    directories = {*contents.get("skills", []), *contents.get("templates", [])}
    files: dict[str, Path] = {}
    for ref in _declared_refs(root, contents):
        link = _link_on(root, ref)
        if link is not None:
            problems.append(f"{link}: symbolic link refused")
            continue
        path = _target(root, ref, problems)
        if path is None:
            continue
        if path.is_file():
            files[ref] = path
        elif path.is_dir() and ref in directories:
            links = symlinks_under(path)
            problems.extend(f"{ref}/{name}: symbolic link refused" for name in links)
            if not links:
                files.update({f"{ref}/{name}": inner for name, inner in files_under(path).items()})
    if len(files) > MAX_FILES:
        problems.append(f"more than {MAX_FILES} files")
    elif sum(p.stat().st_size for p in files.values()) > MAX_BYTES:
        problems.append(f"more than {MAX_BYTES} bytes")
    return dict(sorted(files.items())), problems


def _manifest(root: Path) -> dict[str, Any]:
    """`plugin.yaml`, read and valid against its schema; `PluginError` otherwise."""
    if (root / MANIFEST).is_symlink():
        raise PluginError([f"{MANIFEST}: symbolic link refused"])
    if not (root / MANIFEST).is_file():
        raise PluginError([f"{MANIFEST}: missing at the root of {root.name}"])
    problems: list[str] = []
    manifest = _yaml(root / MANIFEST, MANIFEST, problems)
    problems.extend(_schema_problems(MANIFEST, "plugin.schema.json", manifest))
    if problems:
        raise PluginError(problems)
    assert isinstance(manifest, dict)
    return manifest


def plugin_files(root: Path) -> dict[str, Path]:
    """The files that make the plugin at `root` (relative POSIX path → path, sorted): its manifest, a
    root `README.md` and `LICENSE`, and what `contents` declares. `PluginError` on an invalid manifest
    or a link on a declared path."""
    root = root.resolve()
    files, problems = _declared(root, _manifest(root)["contents"])
    if problems:
        raise PluginError(problems)
    return files


# -- loading ------------------------------------------------------------------------------------------


def _agents(root: Path, refs: list[str], problems: list[str]) -> dict[str, dict[str, Any]]:
    agents: dict[str, dict[str, Any]] = {}
    for ref in refs:
        path = _target(root, ref, problems)
        doc = _check_agent(path, ref, problems) if path is not None else None
        if doc is not None:
            slug = str(doc["agent"].get("slug"))
            if slug in agents:
                problems.append(f"{ref}: agent `{slug}` is shipped twice")
            agents[slug] = doc
    return agents


def _plugin_tracker(manifest: Any) -> str | None:
    connectors = ((manifest or {}).get("requires") or {}).get("connectors") or {}
    tracker = connectors.get("tracker") if isinstance(connectors, dict) else None
    return tracker if isinstance(tracker, str) and tracker.startswith(PLUGIN_TRACKER) else None


def _skills_and_templates(
    root: Path,
    contents: dict[str, list[str]],
    plugin: str,
    servers: dict[str, dict[str, Any]],
    problems: list[str],
) -> None:
    for ref in contents.get("skills", []):
        path = _target(root, ref, problems)
        if path is not None and not (path / "SKILL.md").is_file():
            problems.append(f"{ref}: a skill directory holds a SKILL.md")
    for ref in contents.get("templates", []):
        path = _target(root, ref, problems)
        if path is None:
            continue
        if not (path / "manifest.yaml").is_file():
            problems.append(f"{ref}: a template directory holds a manifest.yaml")
            continue
        where = f"{ref}/manifest.yaml"
        manifest = _yaml(path / "manifest.yaml", where, problems)
        found = _schema_problems(where, "template.schema.json", manifest)
        problems.extend(found)
        tracker = None if found else _plugin_tracker(manifest)
        if tracker is not None:
            owner, _, server = tracker.removeprefix(PLUGIN_TRACKER).partition("/")
            if owner != plugin:
                problems.append(f"{where}: the tracker `{tracker}` is another plugin's than `{plugin}`")
            elif server not in servers:
                problems.append(f"{where}: the tracker `{tracker}` names no server of `{plugin}`")
            elif "tracker" not in (servers[server].get("implements") or []):
                problems.append(
                    f"{where}: the tracker `{tracker}` is a server that does not declare "
                    "`implements: [tracker]`"
                )


def _procedures(
    root: Path, patterns: list[str], plugin: str, servers: dict[str, dict[str, Any]], problems: list[str]
) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        for ref, path in _procedure_files(root, pattern, problems):
            if not path.is_file():
                problems.append(f"{ref}: procedure file missing")
                continue
            proc_id = _check_procedure(path, ref, plugin, servers, problems)
            if proc_id is not None:
                found.append(proc_id)
    return found


def _servers(root: Path, refs: list[str], problems: list[str]) -> dict[str, dict[str, Any]]:
    servers: dict[str, dict[str, Any]] = {}
    for ref in refs:
        path = _target(root, ref, problems)
        server = _check_server(path, ref, problems) if path is not None else None
        if server is not None:
            if server["name"] in servers:
                problems.append(f"{ref}: server `{server['name']}` is declared twice")
            servers[str(server["name"])] = server
    return servers


def load_directory(root: Path) -> Plugin:
    """Validate a plugin directory; `PluginError` lists every problem found. Only what the manifest
    declares is read."""
    root = root.resolve()
    manifest = _manifest(root)
    contents: dict[str, list[str]] = manifest["contents"]
    files, problems = _declared(root, contents)
    if problems:  # nothing is read through a link
        raise PluginError(problems)
    name = str(manifest["metadata"]["name"])

    agents = _agents(root, contents.get("agents", []), problems)
    servers = _servers(root, contents.get("servers", []), problems)
    _skills_and_templates(root, contents, name, servers, problems)
    for ref in contents.get("evals", []):
        path = _target(root, ref, problems)
        if path is not None:
            _check_eval(path, ref, set(agents), problems)
    procedures = _procedures(root, contents.get("procedures", []), name, servers, problems)

    if problems:
        raise PluginError(problems)
    return Plugin(root, manifest, _digest(files), agents, servers, tuple(procedures), tuple(files))


# -- canonical tar, digest, pack ------------------------------------------------------------------------


def _tar(files: dict[str, Path]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, path in sorted(files.items()):
            data = path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _digest(files: dict[str, Path]) -> str:
    return "sha256:" + hashlib.sha256(_tar(files)).hexdigest()


def canonical_tar(root: Path) -> bytes:
    """The plugin's declared files (`plugin_files`) as an uncompressed tar that depends on their paths
    and bytes only."""
    return _tar(plugin_files(root))


def digest(root: Path) -> str:
    """`sha256:<hex>` of the canonical tar."""
    return _digest(plugin_files(root))


def pack(root: Path, output: Path) -> Plugin:
    """Validate, then write the canonical tar gzip-compressed and reproducibly; returns the plugin.
    Only the declared files are written."""
    plugin = load_directory(root)
    files = {name: plugin.root / name for name in plugin.files}
    with output.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write(_tar(files))
    return plugin


def _safe_extract(archive: Path, target: Path) -> None:
    problems: list[str] = []
    total = 0
    with tarfile.open(archive, mode="r:gz") as tar:
        members = tar.getmembers()
        if len(members) > MAX_FILES:
            raise PluginError([f"more than {MAX_FILES} entries"])
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                problems.append(f"{member.name}: goes outside the plugin's folder")
            elif member.issym() or member.islnk():
                problems.append(f"{member.name}: symbolic link refused")
            elif not (member.isfile() or member.isdir()):
                problems.append(f"{member.name}: not a regular file")
            total += max(member.size, 0)
        if total > MAX_BYTES:
            problems.append(f"more than {MAX_BYTES} bytes")
        if problems:
            raise PluginError(problems)
        tar.extractall(target, filter="data")


@contextmanager
def opened(path: Path) -> Iterator[Plugin]:
    """A validated plugin from a directory or a `.tar.gz`; an archive is unpacked into a temporary
    directory that lives as long as the context."""
    if path.is_dir():
        yield load_directory(path)
        return
    with tempfile.TemporaryDirectory(prefix="choregos-plugin-") as tmp:
        try:
            _safe_extract(path, Path(tmp))
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise PluginError([f"{path.name}: not a readable .tar.gz ({exc.__class__.__name__})"]) from exc
        yield load_directory(Path(tmp))


# -- index ------------------------------------------------------------------------------------------------


def build_index(
    plugins_dir: Path,
    *,
    oci_repository: str | None = None,
    git_url: str | None = None,
    git_commit: str | None = None,
    git_path: str = "",
) -> dict[str, Any]:
    """An `index.yaml` (plugin-index.schema.json) for every plugin directory directly under
    `plugins_dir`: located in an OCI repository (`<repo>/<name>:<version>`) or in a git commit."""
    if (oci_repository is None) == (git_url is None):
        raise PluginError(
            ["an index locates its plugins in an OCI repository or in a git commit, one of them"]
        )
    if git_url is not None and git_commit is None:
        raise PluginError(["a git index names its commit"])
    problems: list[str] = []
    plugins: dict[str, Any] = {}
    for child in sorted(p for p in plugins_dir.iterdir() if (p / MANIFEST).is_file()):
        try:
            plugin = load_directory(child)
        except PluginError as exc:
            problems.extend(f"{child.name}/{p}" for p in exc.problems)
            continue
        if plugin.name in plugins:
            problems.append(f"{child.name}: plugin `{plugin.name}` is in two directories")
            continue
        release: dict[str, Any] = {"digest": plugin.digest, "core": plugin.core}
        release["description"] = str(plugin.manifest["metadata"]["description"])
        if plugin.agents:
            # A workflow catalogue names the plugin of a missing agent from the index (ADR 0111 §2).
            release["agents"] = sorted(plugin.agents)
        if oci_repository is not None:
            release["oci"] = f"{oci_repository.rstrip('/')}/{plugin.name}:{plugin.version}"
        else:
            where = f"{git_path.strip('/')}/{child.name}" if git_path.strip("/") else child.name
            release["git"] = {"url": git_url, "commit": git_commit, "path": where}
        plugins[plugin.name] = {"versions": {plugin.version: release}}
    if not plugins and not problems:
        problems.append(f"{plugins_dir}: holds no plugin directory")
    index = {"apiVersion": "choregos/v1", "kind": "PluginIndex", "plugins": plugins}
    problems.extend(_schema_problems("index.yaml", "plugin-index.schema.json", index))
    if problems:
        raise PluginError(problems)
    return index


__all__ = [
    "MANIFEST",
    "MAX_BYTES",
    "MAX_FILES",
    "PLUGIN_TRACKER",
    "TRACKER_PROFILE",
    "Plugin",
    "PluginError",
    "build_index",
    "canonical_tar",
    "digest",
    "load_directory",
    "opened",
    "pack",
    "plugin_files",
]
