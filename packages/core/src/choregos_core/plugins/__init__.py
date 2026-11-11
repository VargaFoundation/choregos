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
- each template's `manifest.yaml` against `template.schema.json`, and each source of a knowledge base it
  ships (`defaults.knowledge`, S51-06) that reads a template input names an input the template declares;
- each eval suite against `plugin-evals.schema.json`, naming an agent of the plugin, a case's
  `input.fields` within the API's 64 KiB of JSON;
- each matrix case (`contents.matrix_cases`, #1372), an agent case of the core's eval matrix in the format
  `choregos_playbooks.evals.agents` reads: a mapping with a `task`, an agent of the plugin (or its own
  `agent_spec`), its `tools_cells` among `mcp` and `scripts`, each connector `plugin:<plugin>/<server>`
  naming this plugin and a server of it, and no two cases of one name. The matrix plays it; the
  installer keeps it and never plays it;
- each procedure through the ADR 0067 parser (`choregos_core.procedures.parse`), and each
  `{{plugin_webhook_url:<plugin>/<server>}}` it holds names this plugin and a server of it with a webhook;
- each template that takes its tracker from a plugin (`requires.connectors.tracker:
  plugin:<plugin>/<server>`) names this plugin and a server of it that `implements: [tracker]`;
- each server file against `plugin-server.schema.json`, plus what a schema cannot say (unique
  operation names, a compensation that names an operation of the same server, egress fields that the
  configuration schema declares, and — for a server that `implements: [tracker]` — every tool of the
  tracker profile declared with its access, `TRACKER_PROFILE`) — and, for a server that declares its
  `fake` (#1372), variables that are neither the platform's (`CHOREGOS_*`) nor any its configuration
  reaches, so that no administrator ever switches a server to its fake.
- each assistant file (`contents.assistants`, ADR 0133) against `assistant.schema.json`, then what the
  schema cannot say (`choregos_core.plugins.assistants`): its agent is one of the plugin's and converses,
  its instructions read only declared configuration, its agent's tools are its declared needs, its subject
  memories have a resolver, its tasks read only, and the files it names are present — and ship with the
  plugin: an assistant's knowledge seeds, task workflows and eval suites are declared files.

Loading also WARNS, without refusing (`Plugin.warnings`): an agent that reads a knowledge base
(`spec.knowledge`) no template of the plugin ships (`defaults.knowledge`) runs only in an organisation
that has that base and serves it to the project — the run stops otherwise (ADR 0117).

The plugin's identity is its **content digest**: the sha256 of its canonical tar (its declared files
only, entries sorted by path, mode 0644, modification time 0, owner and group 0). `pack` writes that tar
gzip-compressed, itself reproducible (gzip header time 0, no file name).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
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
from . import assistants as _assistant_rules

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
#: An eval case's `input.fields`, as JSON: the API's `AgentSampleInput` cap (S51-04, S51-10).
MAX_CASE_FIELDS_BYTES = 64 * 1024
#: A connector of a matrix case that is a plugin's server: `plugin:<plugin>/<server>` (#1372).
PLUGIN_CONNECTOR = "plugin:"
#: The doors a matrix case may be played through (`choregos_playbooks.evals.agents.TOOLS_CELLS`).
MATRIX_CELLS = ("mcp", "scripts")
#: The scenes a server's fake serves when its `fake` names none.
DEFAULT_FAKE_SCENES = ("default",)


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
    #: its matrix cases (`contents.matrix_cases`, #1372): file stem -> the case, its `name` the stem
    matrix_cases: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: the declared files, relative POSIX paths, sorted: what the digest covers and `pack` writes
    files: tuple[str, ...] = ()
    #: what loads but may not run as shipped (ADR 0117): said, never refused
    warnings: tuple[str, ...] = ()
    #: its assistants (`contents.assistants`, ADR 0133): `metadata.name` -> the assistant document
    assistants: dict[str, dict[str, Any]] = field(default_factory=dict)

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
    _check_fake(doc, ref, problems)
    return doc


def configuration_variables(doc: dict[str, Any]) -> dict[str, str]:
    """field -> the environment variable a server's configuration reaches it as: `x-env`, else the
    upper-cased field name (the platform's rule, ADR 0096)."""
    properties = (doc.get("config_schema") or {}).get("properties") or {}
    names: dict[str, str] = {}
    for name in [*properties, *(doc.get("secret_fields") or [])]:
        declared = properties.get(name)
        env = declared.get("x-env") if isinstance(declared, dict) else None
        names[str(name)] = str(env or str(name).upper())
    return names


def fake_scenes(doc: dict[str, Any]) -> tuple[str, ...]:
    """The scenes a server's fake serves (`fake.scenes`, `default` when absent); none without a fake."""
    fake = doc.get("fake")
    if not isinstance(fake, dict):
        return ()
    return tuple(str(s) for s in fake.get("scenes") or DEFAULT_FAKE_SCENES)


def _check_fake(doc: dict[str, Any], ref: str, problems: list[str]) -> None:
    """A server's `fake` (#1372) switches it to its fake through variables no administrator reaches."""
    fake = doc.get("fake")
    if not isinstance(fake, dict):
        return
    reached = {env: name for name, env in configuration_variables(doc).items()}
    for env in sorted(fake.get("env") or {}):
        if env.startswith("CHOREGOS_"):
            problems.append(f"{ref}: fake variable `{env}` is the platform's (`CHOREGOS_*`)")
        elif env in reached:
            problems.append(
                f"{ref}: fake variable `{env}` is what the configuration field `{reached[env]}` reaches: "
                "an administrator would switch the server to its fake"
            )


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
    for index, case in enumerate(doc.get("cases") or []):
        fields = (case.get("input") or {}).get("fields")
        if fields and len(json.dumps(fields, default=str)) > MAX_CASE_FIELDS_BYTES:
            problems.append(
                f"{ref}:cases/{index}/input/fields: at most {MAX_CASE_FIELDS_BYTES // 1024} KiB of JSON"
            )
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


def _check_matrix_case(
    path: Path,
    ref: str,
    plugin: str,
    agents: set[str],
    servers: dict[str, dict[str, Any]],
    problems: list[str],
) -> dict[str, Any] | None:
    """A matrix case (#1372): only what the matrix needs to find its agent, its doors and its
    connectors is checked here; the rest is the core's agent-case format, which the matrix reads."""
    if not path.is_file():
        problems.append(f"{ref}: matrix case file missing")
        return None
    doc = _yaml(path, ref, problems)
    if not isinstance(doc, dict):
        problems.append(f"{ref}: a matrix case is a mapping (the core's agent-case format)")
        return None
    found: list[str] = []
    if not isinstance(doc.get("task"), str) or not doc["task"].strip():
        found.append(f"{ref}: a matrix case has a `task`")
    agent, own = doc.get("agent"), doc.get("agent_spec")
    if not isinstance(agent, str) or not re.fullmatch(AGENT_SLUG, agent):
        found.append(f"{ref}: `agent` is an agent slug ({AGENT_SLUG})")
    elif own is None and agent not in agents:
        found.append(f"{ref}: plays `{agent}`, which the plugin does not ship (nor the case, `agent_spec`)")
    if own is not None and not isinstance(own, dict):
        found.append(f"{ref}: `agent_spec` is a mapping")
    cells = doc.get("tools_cells", ["mcp"])
    if not isinstance(cells, list) or not cells or any(c not in MATRIX_CELLS for c in cells):
        found.append(f"{ref}: `tools_cells` lists doors among {', '.join(MATRIX_CELLS)}")
    found.extend(_matrix_connectors(doc.get("connectors") or {}, ref, plugin, servers))
    problems.extend(found)
    if found:
        return None
    return {**doc, "name": path.stem}


def _matrix_connectors(
    connectors: Any, ref: str, plugin: str, servers: dict[str, dict[str, Any]]
) -> list[str]:
    """A matrix case's connectors: a plugin's server names this plugin, a server of it, by its name."""
    if not isinstance(connectors, dict):
        return [f"{ref}: `connectors` maps a name to `{{kind, scene}}`"]
    found: list[str] = []
    for name, declared in sorted(connectors.items()):
        kind = declared.get("kind") if isinstance(declared, dict) else None
        if not isinstance(kind, str) or not kind:
            found.append(f"{ref}: the connector `{name}` has no `kind`")
            continue
        if not kind.startswith(PLUGIN_CONNECTOR):
            continue  # a scene of the core's fakes, which the matrix checks when it plays the case
        owner, _, server = kind.removeprefix(PLUGIN_CONNECTOR).partition("/")
        if owner != plugin:
            found.append(
                f"{ref}: the connector `{name}` is `{kind}`, another plugin's server than `{plugin}`'s"
            )
        elif server not in servers:
            found.append(f"{ref}: the connector `{name}` is `{kind}`, a server `{plugin}` does not ship")
        elif name != server:
            found.append(
                f"{ref}: the connector `{name}` is the server `{server}`: an agent's tools are "
                f"`<server>__<operation>`, so the case names it `{server}`"
            )
    return found


def _matrix_cases(
    root: Path,
    refs: list[str],
    plugin: str,
    agents: set[str],
    servers: dict[str, dict[str, Any]],
    problems: list[str],
) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for ref in refs:
        path = _target(root, ref, problems)
        case = _check_matrix_case(path, ref, plugin, agents, servers, problems) if path is not None else None
        if case is not None:
            if case["name"] in cases:
                problems.append(f"{ref}: matrix case `{case['name']}` is declared twice")
            cases[str(case["name"])] = case
    return cases


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


def _assistant_named(root: Path, contents: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """The files and directories the plugin's assistants name (ADR 0133): they ship with the plugin.
    An assistant file that is a link, outside the plugin or unreadable names nothing here; its own
    checks say why."""
    files: list[str] = []
    directories: list[str] = []
    for ref in contents.get("assistants", []):
        try:
            path = inside(root, ref)
        except PathOutsideError:
            continue
        if _link_on(root, ref) is not None or not path.is_file():
            continue
        named, seeds = _assistant_rules.named_files(_yaml(path, ref, []))
        files.extend(named)
        directories.extend(seeds)
    return files, directories


def _declared_refs(root: Path, contents: dict[str, list[str]]) -> list[str]:
    refs = [MANIFEST, *(n for n in ROOT_FILES if (root / n).is_symlink() or (root / n).is_file())]
    for kind in ("agents", "evals", "servers", "skills", "templates", "matrix_cases", "assistants"):
        refs.extend(contents.get(kind, []))
    named, seeds = _assistant_named(root, contents)
    refs.extend([*named, *seeds])
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
    directories = {
        *contents.get("skills", []),
        *contents.get("templates", []),
        *_assistant_named(root, contents)[1],
    }
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


def _template_knowledge(manifest: dict[str, Any], where: str, problems: list[str]) -> set[str]:
    """The slugs of the knowledge bases a (schema-valid) template ships; a source reading an input the
    template does not declare is a problem (S51-06)."""
    declared = {str(i.get("name")) for i in manifest.get("inputs") or []}
    shipped: set[str] = set()
    for index, base in enumerate((manifest.get("defaults") or {}).get("knowledge") or []):
        shipped.add(str(base["slug"]))
        for source in base.get("sources") or []:
            name = (source.get("config") or {}).get("url_from_input")
            if name is not None and name not in declared:
                problems.append(
                    f"{where}:defaults/knowledge/{index}: the source `{source.get('name')}` reads the input "
                    f"`{name}`, which the template does not declare"
                )
    return shipped


def _skills_and_templates(
    root: Path,
    contents: dict[str, list[str]],
    plugin: str,
    servers: dict[str, dict[str, Any]],
    problems: list[str],
) -> set[str]:
    """Checks the skills and templates; returns the knowledge bases the templates ship, by slug."""
    knowledge: set[str] = set()
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
        if not found:
            knowledge |= _template_knowledge(manifest, where, problems)
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
    return knowledge


def _knowledge_warnings(agents: dict[str, dict[str, Any]], shipped: set[str]) -> list[str]:
    """An agent reading a base no template of the plugin ships: it runs only where the organisation
    has that base, served to the project (ADR 0117)."""
    warnings = []
    for slug, doc in sorted(agents.items()):
        spec = (doc.get("agent") or {}).get("spec") or {}
        for ref in spec.get("knowledge") or []:
            base = str((ref or {}).get("slug") if isinstance(ref, dict) else ref)
            if base not in shipped:
                warnings.append(
                    f"agent `{slug}` reads the knowledge base `{base}`, which no template of this plugin "
                    "ships (`defaults.knowledge`): its runs stop unless the organisation has that base "
                    "and serves it to the project"
                )
    return warnings


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


def _conversation_suites(doc: dict[str, Any], root: Path, problems: list[str]) -> None:
    """Each eval file an assistant names, against `conversation-evals.schema.json`, then against the
    assistant's tools. A missing file or a bad path is reported by the assistant's own check."""
    for ref in doc.get("evals") or []:
        path = _target(root, ref, [])
        if path is None or not path.is_file():
            continue
        suite = _yaml(path, ref, problems)
        found = _schema_problems(ref, "conversation-evals.schema.json", suite)
        if found:
            problems.extend(found)
            continue
        assert isinstance(suite, dict)
        _assistant_rules.check_suite(suite, ref, doc, problems)


def suite_problems(suite: Any, ref: str, doc: dict[str, Any] | None = None) -> list[str]:
    """What is wrong with a conversation suite: the schema first, then (with the assistant's document) its
    agent, its case names and its tools. Empty when it is valid. Used by the loader's rules, and by the API
    when an owner adds a case to a suite (S63-17)."""
    found = _schema_problems(ref, "conversation-evals.schema.json", suite)
    if found or doc is None:
        return found
    problems: list[str] = []
    _assistant_rules.check_suite(suite, ref, doc, problems)
    return problems


def _assistants(
    root: Path, refs: list[str], agents: dict[str, dict[str, Any]], problems: list[str]
) -> dict[str, dict[str, Any]]:
    """Each assistant file against `assistant.schema.json`, then what the schema cannot say
    (`choregos_core.plugins.assistants`)."""
    found: dict[str, dict[str, Any]] = {}
    for ref in refs:
        path = _target(root, ref, problems)
        if path is None:
            continue
        if not path.is_file():
            problems.append(f"{ref}: assistant file missing")
            continue
        doc = _yaml(path, ref, problems)
        schema = _schema_problems(ref, "assistant.schema.json", doc)
        if schema:
            problems.extend(schema)
            continue
        assert isinstance(doc, dict)
        _assistant_rules.check(doc, ref, agents, root, _target, problems)
        _conversation_suites(doc, root, problems)
        name = str(doc["metadata"]["name"])
        if name in found:
            problems.append(f"{ref}:metadata/name: assistant `{name}` is shipped twice")
        found[name] = doc
    return found


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
    shipped = _skills_and_templates(root, contents, name, servers, problems)
    for ref in contents.get("evals", []):
        path = _target(root, ref, problems)
        if path is not None:
            _check_eval(path, ref, set(agents), problems)
    procedures = _procedures(root, contents.get("procedures", []), name, servers, problems)
    matrix = _matrix_cases(root, contents.get("matrix_cases", []), name, set(agents), servers, problems)
    assistants = _assistants(root, contents.get("assistants", []), agents, problems)

    if problems:
        raise PluginError(problems)
    return Plugin(
        root,
        manifest,
        _digest(files),
        agents,
        servers,
        tuple(procedures),
        matrix,
        tuple(files),
        tuple(_knowledge_warnings(agents, shipped)),
        assistants,
    )


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
    "DEFAULT_FAKE_SCENES",
    "MANIFEST",
    "MATRIX_CELLS",
    "MAX_BYTES",
    "MAX_FILES",
    "PLUGIN_CONNECTOR",
    "PLUGIN_TRACKER",
    "TRACKER_PROFILE",
    "Plugin",
    "PluginError",
    "build_index",
    "canonical_tar",
    "configuration_variables",
    "digest",
    "fake_scenes",
    "load_directory",
    "opened",
    "pack",
    "plugin_files",
    "suite_problems",
]
