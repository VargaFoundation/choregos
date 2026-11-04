# SPDX-License-Identifier: Apache-2.0
"""The stack templates: where they live, and what their manifest delivers to a new project.

A template is a `templates/<name>/` folder, shipped with the platform or named by
`CHOREGOS_TEMPLATES_DIR`, or a `templates` row published by an administrator, which wins. Under
`defaults`, its manifest says the workflows of a project born of it, the default and the routing
(ADR 0031), and the policy. `ensure_defaults` used to ignore it: every project received
`default-simple`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from choregos_core import preset_yaml, template_yaml
from choregos_core.compat import alias_names
from choregos_core.paths import PathOutsideError, files_under, inside, symlinks_under
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Template
from ..errors import unprocessable
from .template_knowledge import parse_template_knowledge

WORKFLOW_WITHOUT_TEMPLATE = "template:default-simple@1"
#: Without a template and without a repository: a workflow that delivers no code (ADR 0110).
WORKFLOW_WITHOUT_REPOSITORY = "template:default-request@1"
POLICY_WITHOUT_TEMPLATE = "preset:solo"


def templates_directory() -> Path:
    """The same folder as the orchestrator, read from the same setting."""
    configured = os.environ.get("CHOREGOS_TEMPLATES_DIR", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parents[5] / "templates"


@dataclass(frozen=True)
class Delivery:
    """What a template delivers to a new project: YAML sources, not yet published."""

    workflows: list[str]
    policy: str
    default: str | None = None
    routing: list[dict[str, Any]] = field(default_factory=list)
    #: The agents (their `AgentCreate` document) and the skills (their files) it installs into the
    #: organisation (S20-07).
    agents: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, str]] = field(default_factory=list)
    #: Among these agents, those the template takes from the catalogue: slug -> `catalogue:<slug>@<version>`,
    #: the provenance of a direct install (S39-25).
    catalogue_agents: dict[str, str] = field(default_factory=dict)
    #: What extensions install (S20-09): the installer's name -> the files of its folder.
    extensions: dict[str, dict[str, str]] = field(default_factory=dict)
    #: The knowledge bases its agents read (S51-06, ADR 0117), checked, and the inputs it declares.
    knowledge: list[Any] = field(default_factory=list)
    inputs: list[dict[str, Any]] = field(default_factory=list)


async def template_manifest(
    session: AsyncSession, template_ref: str | None, org_id: str | None = None
) -> tuple[dict[str, Any], Path | None]:
    """The manifest of `name[@version]`, and the template's folder when it is on disk.

    A template published in the database has no folder: it can only deliver workflows of the core.
    `plugin:<plugin>/<template>` names a template an installed plugin of `org_id` brought: it carries its
    files (ADR 0093, S43-04).
    """
    if not template_ref:
        return {}, None
    if template_ref.startswith("plugin:"):
        from .plugin_installer import plugin_template

        manifest, folder = await plugin_template(session, org_id, template_ref)
        return manifest, folder
    name, _, version = template_ref.partition("@")
    query = select(Template).where(Template.name == name)
    if version:
        query = query.where(Template.version == version)
    row = (await session.execute(query.order_by(Template.created_at.desc()).limit(1))).scalar_one_or_none()
    if row is not None:
        return dict(row.manifest or {}), None
    folder = templates_directory() / name
    path = folder / "manifest.yaml"
    if not path.is_file():
        raise unprocessable(f"template `{template_ref}` not found")
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {}), folder


def delivery(manifest: dict[str, Any], folder: Path | None, *, has_repository: bool = True) -> Delivery:
    """The sources a manifest ships; without a template, `solo` and `default-simple` — or
    `default-request` for a project that names no repository (ADR 0110). A template's own workflows
    always win."""
    defaults = manifest.get("defaults") or {}
    refs = list(defaults.get("workflows") or ([defaults["workflow"]] if defaults.get("workflow") else []))
    fallback = WORKFLOW_WITHOUT_TEMPLATE if has_repository else WORKFLOW_WITHOUT_REPOSITORY
    return Delivery(
        workflows=[_workflow_source(ref, folder) for ref in refs or [fallback]],
        policy=_policy_source(str(defaults.get("policy") or POLICY_WITHOUT_TEMPLATE), folder),
        default=defaults.get("default_workflow"),
        routing=list(defaults.get("routing") or []),
        agents=[_agent_document(str(ref), folder) for ref in defaults.get("agents") or []],
        skills=[_skill_files(str(ref), folder) for ref in defaults.get("skills") or []],
        catalogue_agents=_catalogue_agents(defaults.get("agents") or []),
        extensions={
            str(name): _folder_files(str(ref), folder, f"extension `{name}`")
            for name, ref in (defaults.get("extensions") or {}).items()
        },
        knowledge=parse_template_knowledge(
            defaults.get("knowledge"),
            manifest.get("inputs"),
            str((manifest.get("metadata") or {}).get("name") or "?"),
        ),
        inputs=[dict(i) for i in manifest.get("inputs") or [] if isinstance(i, dict)],
    )


def shipped_workflows(manifest: dict[str, Any], folder: Path | None) -> list[tuple[str, str]]:
    """`(ref, YAML source)` of each workflow the manifest ships today, in its order (S38-20).

    The same sources `delivery` gives a new project, with the reference each comes from, so that a
    project born of this template can be told which file to publish again."""
    defaults = manifest.get("defaults") or {}
    refs = list(defaults.get("workflows") or ([defaults["workflow"]] if defaults.get("workflow") else []))
    return [(str(ref), _workflow_source(str(ref), folder)) for ref in refs or [WORKFLOW_WITHOUT_TEMPLATE]]


def _template_path(ref: str, folder: Path | None) -> Path:
    """A path the manifest names: inside the template's folder, never outside."""
    if folder is None:
        raise unprocessable(f"`{ref}`: a template published in the database carries no file")
    try:
        return inside(folder, ref)
    except PathOutsideError as outside:
        raise unprocessable(f"`{ref}` goes outside the template's folder") from outside


#: A template names an agent or a skill of the platform's catalogue as `catalogue:<name>` (ADR 0040).
#: The catalogue is the plugins' since S43-09 (ADR 0093 §6): the name resolves through them.
CATALOGUE_PREFIX = "catalogue:"


def _catalogue_agents(refs: list[Any]) -> dict[str, str]:
    """slug -> `plugin:<plugin>/<slug>@<version>`: the provenance a direct install would write."""
    from .agent_catalogue import offline_catalogue

    view = offline_catalogue()
    provenances: dict[str, str] = {}
    for ref in map(str, refs):
        found = view.entry(ref.removeprefix(CATALOGUE_PREFIX)) if ref.startswith(CATALOGUE_PREFIX) else None
        if found is not None:
            provenances[found.slug] = found.provenance
    return provenances


def _agent_document(ref: str, folder: Path | None) -> dict[str, Any]:
    if ref.startswith(CATALOGUE_PREFIX):
        from .agent_catalogue import offline_catalogue

        found = offline_catalogue().entry(ref.removeprefix(CATALOGUE_PREFIX))
        if found is None:
            raise unprocessable(f"`{ref}`: the platform's catalogue has no such agent")
        return found.document
    path = _template_path(ref, folder)
    if not path.is_file():
        raise unprocessable(f"`{ref}`: agent missing from the template")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise unprocessable(f"`{ref}`: an agent is described by an object (`slug`, `display_name`, `spec`)")
    return document


def _skill_files(ref: str, folder: Path | None) -> dict[str, str]:
    if ref.startswith(CATALOGUE_PREFIX):
        from .agent_catalogue import offline_catalogue

        skill = offline_catalogue().skills.get(ref.removeprefix(CATALOGUE_PREFIX))
        if skill is None:
            raise unprocessable(f"`{ref}`: the platform's catalogue has no such skill")
        return dict(skill.files)
    return _folder_files(ref, folder, "skill")


def _folder_files(ref: str, folder: Path | None, what: str) -> dict[str, str]:
    """The files of a folder of the template, relative path -> text; a symbolic link is refused, as
    in an archive (nothing may be read outside the folder)."""
    root = _template_path(ref, folder)
    if not root.is_dir():
        raise unprocessable(f"`{ref}`: {what} missing from the template")
    links = symlinks_under(root)
    if links:
        raise unprocessable(f"`{ref}`: symbolic link refused ({Path(links[0]).name})")
    return {name: path.read_text(encoding="utf-8") for name, path in files_under(root).items()}


def _workflow_source(ref: str, folder: Path | None) -> str:
    if ref.startswith("template:"):
        name = ref.removeprefix("template:").partition("@")[0]
        try:
            return template_yaml(name)
        except FileNotFoundError as missing:
            raise unprocessable(f"the template ships `{ref}`, which the platform does not know") from missing
    if folder is None:
        raise unprocessable(
            f"`{ref}`: a template published in the database carries no file; name `template:<name>@<v>`"
        )
    path = _template_path(ref, folder)
    if not path.is_file():
        raise unprocessable(f"`{ref}`: file missing from the template")
    return path.read_text(encoding="utf-8")


def _policy_source(ref: str, folder: Path | None) -> str:
    """`preset:<name>`, or a file of the template (`./policy.yaml`): a software-delivery template
    has its budgets and its trains, which no preset carries (S21-22). A path stays in the folder."""
    if not ref.startswith("preset:"):
        path = _template_path(ref, folder)
        if not path.is_file():
            raise unprocessable(f"policy `{ref}`: file missing from the template")
        return path.read_text(encoding="utf-8")
    name = ref.removeprefix("preset:")
    if "/" in name or name.startswith("."):
        raise unprocessable(f"policy `{ref}`: a preset is named, not looked up in a path")
    try:
        return preset_yaml(name)
    except FileNotFoundError as missing:
        raise unprocessable(
            f"the template ships policy `{ref}`, which the platform does not know"
        ) from missing


Livraison = Delivery  # lang: fr-ok alias:internal
livraison = delivery  # lang: fr-ok alias:internal
manifeste_du_gabarit = template_manifest  # lang: fr-ok alias:internal
repertoire_des_gabarits = templates_directory  # lang: fr-ok alias:internal
WORKFLOW_SANS_GABARIT = WORKFLOW_WITHOUT_TEMPLATE  # lang: fr-ok alias:internal
POLITIQUE_SANS_GABARIT = POLICY_WITHOUT_TEMPLATE  # lang: fr-ok alias:internal
PREFIXE_CATALOGUE = CATALOGUE_PREFIX  # lang: fr-ok alias:internal

alias_names(
    __name__,
    {
        "Livraison": "Delivery",  # lang: fr-ok alias:internal
        "livraison": "delivery",  # lang: fr-ok alias:internal
        "manifeste_du_gabarit": "template_manifest",  # lang: fr-ok alias:internal
        "repertoire_des_gabarits": "templates_directory",  # lang: fr-ok alias:internal
        "WORKFLOW_SANS_GABARIT": "WORKFLOW_WITHOUT_TEMPLATE",  # lang: fr-ok alias:internal
        "POLITIQUE_SANS_GABARIT": "POLICY_WITHOUT_TEMPLATE",  # lang: fr-ok alias:internal
        "PREFIXE_CATALOGUE": "CATALOGUE_PREFIX",  # lang: fr-ok alias:internal
    },
)

__all__ = ["Delivery", "delivery", "shipped_workflows", "template_manifest", "templates_directory"]
