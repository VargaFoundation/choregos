# SPDX-License-Identifier: Apache-2.0
"""The agent catalogue the platform offers (ADR 0040), read from plugins (ADR 0093 §6, S43-09).

The review of 2026-10-07 found that software workflows ran IMPLICIT agents; the catalogue made them
registry agents, ready to install. Since S43-09 the core no longer carries them: they ship as plugins
of `VargaFoundation/choregos-agents` (`software-delivery`, `netops`, `authoring`, `mcp-clients`,
`requests`), and
the catalogue is a VIEW over plugins — the deployment's offline directory (the committed
`plugins/offline/` of this repository, `/opt/choregos/plugins` in the API image), plus, in the API,
the plugins an organisation installed (`services/catalogue_d_agents.py`).

The view also holds what the eval matrix reads of a plugin (#1372): its matrix cases
(`contents.matrix_cases`, named `plugin:<plugin>/<case>` by the matrix, `matrix_case`) and its servers'
declarations (`server`), whose operations govern a case's `plugin:<plugin>/<server>` connector.

An entry is a plugin's agent file: `catalogue` (its version, the role it holds, a sentence) and `agent`,
the document `POST /orgs/{org}/agents` accepts. An internal agent declares its five components (ADR 0127,
S60-04): a `model` (a model or a `profile:<tier>`) and `tools.catalogue|extensions|web` and `memories`, each
possibly empty, so that it inherits none of them from the project. It still fixes no runtime: the backend is
the deployment's (`choregos_core.agent_completeness` says what an entry leaves out). Its instructions are preceded by
the work item's context (`_context.md`) when loaded: the context is the core's contract with its own
runtime (what a run hands the agent), so it stays here and a plugin's author writes the task, not the
plumbing.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml

from ..plugins import Plugin, PluginError, opened

FOLDER = Path(__file__).parent
#: The environment variable the API reads for the same directory (`Settings.plugins_offline`).
OFFLINE_ENV = "CHOREGOS_PLUGINS_OFFLINE"
#: Where the API image puts the plugins it ships (`docker/api.Dockerfile`).
IMAGE_OFFLINE = Path("/opt/choregos/plugins")
LOCK = "plugins.lock"


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    """An agent the catalogue offers; `document` is an `AgentCreate`."""

    slug: str
    kind: Literal["internal", "external"]
    version: int
    role: str
    summary: str
    document: dict[str, Any]
    #: For an external client: its identifier on the Integrations page, and where it calls from.
    client: str | None = None
    reach: Literal["always", "cloud"] | None = None
    skills: tuple[str, ...] = field(default_factory=tuple)
    #: The output contract (S33-06): the names a transition that runs this agent declares. Empty for
    #: an entry that does not state it yet.
    outputs: tuple[str, ...] = field(default_factory=tuple)
    #: What it acts with: runner binaries, connectors, skills, knowledge — what an installer must provide.
    tools: tuple[str, ...] = field(default_factory=tuple)
    #: Its result informs a decision that people make: a workflow sends it to a human next.
    human_gate_after: bool = False
    #: Labels people find the agent by (S40-10); installed with it, added on upgrade, never removed.
    tags: tuple[str, ...] = field(default_factory=tuple)
    #: The plugin that ships it and that plugin's version (ADR 0093): its provenance names them.
    plugin: str = ""
    plugin_version: str = ""

    @property
    def digest(self) -> str:
        """What tells two versions of the entry apart: a change of text changes the digest."""
        raw = json.dumps(self.document, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    @property
    def provenance(self) -> str:
        """`plugin:<plugin>/<slug>@<catalogue version>`: the author of what installing it writes."""
        return f"plugin:{self.plugin}/{self.slug}@{self.version}"


@dataclass(frozen=True, slots=True)
class CatalogueSkill:
    name: str
    plugin: str
    plugin_version: str
    files: dict[str, str]

    @property
    def provenance(self) -> str:
        """`plugin:<plugin>/<skill>@<plugin version>`, as the plugin installer writes it."""
        return f"plugin:{self.plugin}/{self.name}@{self.plugin_version}"


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One eval case of a plugin (`evals/<name>.yaml`), for the agent it names."""

    name: str
    plugin: str
    case: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MatrixCase:
    """One matrix case of a plugin (`contents.matrix_cases`, #1372): an agent case of the core's eval
    matrix, `plugin:<plugin>/<name>` to the matrix."""

    name: str
    plugin: str
    plugin_version: str
    case: dict[str, Any]

    @property
    def ref(self) -> str:
        return f"{PLUGIN_CASE}{self.plugin}/{self.name}"


#: How the eval matrix names a plugin's case, and a case its plugin's server (#1372).
PLUGIN_CASE = "plugin:"


def plugin_ref(ref: str) -> tuple[str, str] | None:
    """`plugin:<plugin>/<name>` as `(plugin, name)`; `None` for anything else."""
    if not ref.startswith(PLUGIN_CASE):
        return None
    plugin, _, name = ref.removeprefix(PLUGIN_CASE).partition("/")
    return (plugin, name) if plugin and name and "/" not in name else None


@dataclass(frozen=True, slots=True)
class Catalogue:
    """What a set of plugins offers: agents (internal first, then external, each by name), skills, evals,
    and what the eval matrix reads of them (#1372): their matrix cases and their servers' declarations."""

    entries: tuple[CatalogueEntry, ...] = ()
    skills: dict[str, CatalogueSkill] = field(default_factory=dict)
    evals: tuple[EvalCase, ...] = ()
    #: name -> version of every plugin read
    plugins: dict[str, str] = field(default_factory=dict)
    matrix_cases: tuple[MatrixCase, ...] = ()
    #: (plugin, server) -> the server's file (`plugin-server.schema.json`)
    servers: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    def entry(self, slug: str) -> CatalogueEntry | None:
        return next((e for e in self.entries if e.slug == slug), None)

    def matrix_case(self, ref: str) -> MatrixCase | None:
        """The case the matrix names `plugin:<plugin>/<case>`; `None` when no plugin read ships it."""
        named = plugin_ref(ref)
        if named is None:
            return None
        return next((c for c in self.matrix_cases if (c.plugin, c.name) == named), None)

    def server(self, ref: str) -> dict[str, Any] | None:
        """The declaration of the server a case names `plugin:<plugin>/<server>`; `None` when absent."""
        named = plugin_ref(ref)
        return None if named is None else self.servers.get(named)

    def over(self, base: Catalogue) -> Catalogue:
        """This catalogue's plugins replace `base`'s plugins of the same name (an organisation's installed
        version over the image's); an entry or skill of the same name is this catalogue's."""
        mine = set(self.plugins)
        entries = {e.slug: e for e in base.entries if e.plugin not in mine}
        entries |= {e.slug: e for e in self.entries}
        skills = {n: s for n, s in base.skills.items() if s.plugin not in mine} | dict(self.skills)
        evals = (*(c for c in base.evals if c.plugin not in mine), *self.evals)
        matrix = (*(c for c in base.matrix_cases if c.plugin not in mine), *self.matrix_cases)
        servers = {k: s for k, s in base.servers.items() if k[0] not in mine} | dict(self.servers)
        return Catalogue(
            _ordered(entries.values()),
            skills,
            evals,
            {**base.plugins, **self.plugins},
            matrix,
            servers,
        )


def _ordered(entries: Iterable[CatalogueEntry]) -> tuple[CatalogueEntry, ...]:
    return tuple(sorted(entries, key=lambda e: (e.kind != "internal", e.slug)))


def _context() -> str:
    return (FOLDER / "_context.md").read_text(encoding="utf-8")


def entry_of(raw: dict[str, Any], plugin: str = "", plugin_version: str = "") -> CatalogueEntry:
    """An entry from a catalogue document (`catalogue:` + `agent:`): a plugin's agent file (ADR 0093).
    The context block precedes an internal agent's instructions; an external client keeps its `client`
    and `reach`."""
    meta, document = dict(raw["catalogue"]), dict(raw["agent"])
    kind = document.get("kind", "internal")
    spec = dict(document.get("spec") or {})
    if kind == "internal":
        spec["instructions"] = _context() + spec.get("instructions", "")
    document["spec"] = spec
    return CatalogueEntry(
        slug=document["slug"],
        kind=kind,
        version=int(meta["version"]),
        role=str(meta["role"]),
        summary=str(meta["summary"]),
        document=document,
        client=meta.get("client"),
        reach=meta.get("reach"),
        skills=tuple(str(s["slug"]) for s in spec.get("skills") or []),
        outputs=tuple(str(o) for o in meta.get("outputs") or []),
        tools=tuple(str(t) for t in meta.get("tools") or []),
        human_gate_after=bool(meta.get("human_gate_after", False)),
        tags=tuple(str(t) for t in document.get("tags") or []),
        plugin=plugin,
        plugin_version=plugin_version,
    )


def _texts(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def of_plugin(plugin: Plugin) -> Catalogue:
    """What one loaded plugin offers."""
    contents = dict(plugin.manifest.get("contents") or {})
    entries = [entry_of(doc, plugin.name, plugin.version) for _, doc in sorted(plugin.agents.items())]
    skills = {
        Path(ref).name: CatalogueSkill(Path(ref).name, plugin.name, plugin.version, _texts(plugin.root / ref))
        for ref in contents.get("skills") or []
    }
    evals = tuple(
        EvalCase(
            Path(ref).stem, plugin.name, dict(yaml.safe_load((plugin.root / ref).read_text(encoding="utf-8")))
        )
        for ref in contents.get("evals") or []
    )
    matrix = tuple(
        MatrixCase(name, plugin.name, plugin.version, dict(case))
        for name, case in sorted(plugin.matrix_cases.items())
    )
    servers = {(plugin.name, name): dict(doc) for name, doc in sorted(plugin.servers.items())}
    return Catalogue(_ordered(entries), skills, evals, {plugin.name: plugin.version}, matrix, servers)


def _candidates(directory: Path) -> list[Path]:
    return sorted(
        p
        for p in directory.iterdir()
        if (p.is_dir() and not p.is_symlink() and (p / "plugin.yaml").is_file())
        or (p.is_file() and p.name.endswith(".tar.gz"))
    )


@cache
def _load(directory: Path, stamp: tuple[tuple[str, float], ...]) -> Catalogue:
    catalogue = Catalogue()
    for candidate in _candidates(directory):
        try:
            with opened(candidate) as plugin:
                catalogue = of_plugin(plugin).over(catalogue)
        except PluginError:
            continue  # the API's offline index names it with its reason; the catalogue offers the rest
    return catalogue


def load(directory: Path | None) -> Catalogue:
    """The catalogue of a plugin directory (each `<plugin>/` or `.tar.gz` in it); empty without one.
    Read once per content of the directory."""
    if directory is None or not directory.is_dir():
        return Catalogue()
    stamp = tuple((p.name, p.stat().st_mtime) for p in _candidates(directory))
    return _load(directory.resolve(), stamp)


def offline_directory() -> Path | None:
    """The offline plugin directory: `CHOREGOS_PLUGINS_OFFLINE`, else this repository's
    `plugins/offline/` (a source tree), else the API image's `/opt/choregos/plugins`."""
    configured = os.environ.get(OFFLINE_ENV)
    if configured:
        return Path(configured)
    for parent in FOLDER.resolve().parents:
        candidate = parent / "plugins" / "offline"
        if (candidate / LOCK).is_file():
            return candidate
    return IMAGE_OFFLINE if IMAGE_OFFLINE.is_dir() else None


def catalogue(directory: Path | None = None) -> Catalogue:
    """The offline directory's catalogue (`directory` replaces it)."""
    return load(directory if directory is not None else offline_directory())


def entries() -> tuple[CatalogueEntry, ...]:
    """Internal agents first, then external clients, each in name order."""
    return catalogue().entries


def entry(slug: str) -> CatalogueEntry | None:
    return next((e for e in entries() if e.slug == slug), None)


def catalogue_skills() -> tuple[str, ...]:
    return tuple(sorted(catalogue().skills))


def skill_files(name: str) -> dict[str, str]:
    """The files of a catalogue skill, relative path -> text; `KeyError` when there is none."""
    return dict(catalogue().skills[name].files)


__all__ = [
    "FOLDER",
    "PLUGIN_CASE",
    "Catalogue",
    "CatalogueEntry",
    "CatalogueSkill",
    "EvalCase",
    "MatrixCase",
    "catalogue",
    "catalogue_skills",
    "entries",
    "entry",
    "entry_of",
    "load",
    "of_plugin",
    "offline_directory",
    "plugin_ref",
    "skill_files",
]
