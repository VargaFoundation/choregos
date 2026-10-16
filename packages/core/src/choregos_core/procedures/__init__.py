# SPDX-License-Identifier: Apache-2.0
"""Setup procedures: what a person does at the vendor, then in Choregos, to add one thing (ADR 0067).

A procedure is a Markdown file with a YAML front matter, shipped in this package, one per connector
type, knowledge source kind, MCP-door client and external agent kind. The API serves it with this
deployment's URLs filled in; the console shows it beside the form; `tools/procedures/export_docs.py`
writes the same files to `docs/procedures/`.

The body has fixed sections, in this order, so that a test can hold every procedure to the same
shape and a person always finds "when it fails" in the same place:

    ## Before you start / ## At <vendor> / ## Permissions / ## In Choregos / ## Test it /
    ## When it fails / (## On this platform, added by the deployment)

A deployment adds to them through a directory (`CHOREGOS_PROCEDURES_EXTRA`, a ConfigMap in the
chart) or a `choregos.procedures` entry point: `<id>.site.md` appends "On this platform" to a
procedure, `<new-id>.md` adds a procedure for a type the platform does not know (a plugin's).
"""

from __future__ import annotations

import importlib.metadata
import os
import pathlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlsplit

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..errors import ChoregosError

log = structlog.get_logger(__name__)

ENTRY_POINT_GROUP = "choregos.procedures"
SHIPPED = pathlib.Path(__file__).resolve().parent

#: The section keys, in the order a procedure must have them. `at` is "At <vendor>" whatever the vendor.
SECTION_KEYS = ("before", "at", "permissions", "in_choregos", "test", "fails")
SITE_KEY = "site"
SITE_TITLE = "On this platform"
_TITLES = {
    "before": "Before you start",
    "permissions": "Permissions",
    "in_choregos": "In Choregos",
    "test": "Test it",
    "fails": "When it fails",
}
#: What a procedure applies to: exactly one of these...
PRIMARY_KEYS = ("connector", "source", "agent", "door_client")
#: ...and optionally one of these, which makes it a preset: it wins over the generic one when asked.
QUALIFIER_KEYS = ("card_host", "preset")

#: The webhook routes of the API (`routers/webhooks.py`).
WEBHOOK_SOURCES = ("github", "jira", "gitlab", "argocd", "tekton", "alertmanager")
#: `{{name}}` or `{{name:argument}}`: the only forms a body may use, and the only names.
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)(?::([a-z_]+))?\s*\}\}")
PLAIN_PLACEHOLDERS = frozenset({"api_url", "public_url", "org"})
PARAMETRISED_PLACEHOLDERS = {
    "webhook_url": frozenset(WEBHOOK_SOURCES),
    "oauth_callback": frozenset({"remote"}),
}

_FENCE = re.compile(r"^\s*(```|~~~)")
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)[^)]*\)")
#: A secret-shaped string never belongs in a procedure (the gitleaks gate is not the only one).
SECRET_SHAPES = (
    re.compile(r"xox[bpas]-[A-Za-z0-9-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"ATATT[A-Za-z0-9_=-]{10,}"),
    re.compile(r"\b[0-9a-f]{40}\b"),
)


class ProcedureError(ChoregosError):
    """A procedure file that does not hold the contract. Raised for files that ship with the platform."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VendorDoc(_Model):
    title: str = Field(min_length=1)
    url: str

    @field_validator("url")
    @classmethod
    def _https(cls, value: str) -> str:
        if urlsplit(value).scheme != "https":
            raise ValueError(f"a vendor document is linked over https: {value}")
        return value


class Front(_Model):
    """The front matter of a procedure."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    title: str = Field(min_length=1, max_length=120)
    applies_to: dict[str, str]
    audience: Literal["org_admin", "project_owner", "operator", "everyone"]
    #: +1 on any change of substance, so a reader can tell a procedure moved.
    version: int = Field(ge=1)
    #: The day someone last compared it with the vendor's own pages.
    reviewed: date
    vendor_docs: list[VendorDoc] = Field(default_factory=list)
    #: Placeholders the panel shows as copy boxes, e.g. `webhook_url:jira`.
    values: list[str] = Field(default_factory=list)
    #: Hosts this setup needs to reach (the egress allowlist of S39-22).
    egress: list[str] = Field(default_factory=list)
    #: The form fields the procedure talks about, with what to paste: checked against the type's schema.
    fields: dict[str, str] = Field(default_factory=dict)
    #: An in-app route that IS the procedure (the MCP-door clients' page), else null.
    link: str | None = None

    @field_validator("applies_to")
    @classmethod
    def _target(cls, value: dict[str, str]) -> dict[str, str]:
        primaries = [k for k in value if k in PRIMARY_KEYS]
        unknown = [k for k in value if k not in PRIMARY_KEYS and k not in QUALIFIER_KEYS]
        if len(primaries) != 1 or unknown:
            raise ValueError(
                f"applies_to names exactly one of {PRIMARY_KEYS}, plus optionally {QUALIFIER_KEYS}: {value}"
            )
        return value

    @field_validator("values")
    @classmethod
    def _known_values(cls, value: list[str]) -> list[str]:
        for item in value:
            name, _, argument = item.partition(":")
            _check_placeholder(name, argument or None)
        return value

    @field_validator("link")
    @classmethod
    def _route(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("/"):
            raise ValueError("`link` is an in-app route, starting with /")
        return value


@dataclass(frozen=True, slots=True)
class Section:
    key: str
    title: str
    markdown: str


@dataclass(frozen=True, slots=True)
class Procedure:
    front: Front
    sections: tuple[Section, ...]
    #: Where it came from: `shipped`, `extra` (a deployment's directory) or a plugin's entry point.
    origin: str = "shipped"

    @property
    def id(self) -> str:
        return self.front.id

    @property
    def target(self) -> tuple[str, str]:
        """The `(key, value)` it is looked up by, e.g. `("connector", "documents/sharepoint")`."""
        key = next(k for k in self.front.applies_to if k in PRIMARY_KEYS)
        return key, self.front.applies_to[key]

    @property
    def qualifiers(self) -> dict[str, str]:
        return {k: v for k, v in self.front.applies_to.items() if k in QUALIFIER_KEYS}

    @property
    def body(self) -> str:
        return "\n\n".join(f"## {s.title}\n\n{s.markdown}" for s in self.sections)


@dataclass(frozen=True, slots=True)
class Context:
    """What the deployment fills in: the placeholders of a body."""

    api_url: str
    public_url: str
    org: str = ""


@dataclass(slots=True)
class Catalogue:
    procedures: dict[str, Procedure] = field(default_factory=dict)
    #: Files of a deployment's directory that were refused, and why (the shipped ones raise instead).
    problems: list[str] = field(default_factory=list)

    def get(self, procedure_id: str) -> Procedure | None:
        return self.procedures.get(procedure_id)

    def for_target(
        self, target: str, *, card_host: str | None = None, preset: str | None = None
    ) -> Procedure | None:
        """The best procedure for `connector:documents/sharepoint`, `source:web`, `agent:remote.a2a`...

        A preset (one that names a `card_host` or a `preset`) wins when the request carries that
        qualifier; the generic one is the answer otherwise. A request that carries a qualifier no
        preset matches gets the generic one too.
        """
        key, _, value = target.partition(":")
        if key not in PRIMARY_KEYS or not value:
            return None
        asked = {k: v for k, v in (("card_host", card_host), ("preset", preset)) if v}
        generic: Procedure | None = None
        for proc in sorted(self.procedures.values(), key=lambda p: p.id):
            if proc.target != (key, value):
                continue
            qualifiers = proc.qualifiers
            if not qualifiers:
                generic = proc
            elif all(asked.get(k) == v for k, v in qualifiers.items()):
                return proc
        return generic


def _check_placeholder(name: str, argument: str | None) -> None:
    if name in PLAIN_PLACEHOLDERS and argument is None:
        return
    if name in PARAMETRISED_PLACEHOLDERS and argument in PARAMETRISED_PLACEHOLDERS[name]:
        return
    shown = name if argument is None else f"{name}:{argument}"
    raise ValueError(f"unknown placeholder {{{{{shown}}}}}")


def placeholders_in(text: str) -> list[tuple[str, str | None]]:
    return [(m.group(1), m.group(2)) for m in PLACEHOLDER.finditer(text)]


def resolve_placeholder(name: str, argument: str | None, ctx: Context) -> str:
    _check_placeholder(name, argument)
    api = ctx.api_url.rstrip("/")
    if name == "api_url":
        return api
    if name == "public_url":
        return ctx.public_url.rstrip("/")
    if name == "org":
        return ctx.org
    if name == "webhook_url":
        return f"{api}/api/v1/webhooks/{argument}"
    return f"{api}/api/v1/remote-credentials/oauth/callback"  # oauth_callback:remote


def fill(text: str, ctx: Context) -> str:
    """Substitute every placeholder; an unknown one is an error, never an empty string."""
    return PLACEHOLDER.sub(lambda m: resolve_placeholder(m.group(1), m.group(2), ctx), text)


def values_of(proc: Procedure, ctx: Context) -> dict[str, str]:
    """The copy boxes: `{"webhook_url:jira": "https://…/api/v1/webhooks/jira"}`."""
    out: dict[str, str] = {}
    for item in proc.front.values:
        name, _, argument = item.partition(":")
        out[item] = resolve_placeholder(name, argument or None, ctx)
    return out


def render(proc: Procedure, ctx: Context) -> tuple[Section, ...]:
    return tuple(Section(s.key, s.title, fill(s.markdown, ctx)) for s in proc.sections)


def links_in(markdown: str) -> list[str]:
    """Every Markdown link target outside code fences."""
    out: list[str] = []
    fenced = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.extend(_LINK.findall(line))
    return out


def _split_front(text: str, where: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ProcedureError(f"{where}: a procedure starts with a `---` front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ProcedureError(f"{where}: the front matter is not closed by a `---` line")
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        raise ProcedureError(f"{where}: the front matter is not YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ProcedureError(f"{where}: the front matter is not a mapping")
    return data, text[end + 5 :]


def _sections(body: str, where: str, *, allow_site: bool) -> tuple[Section, ...]:
    chunks: list[tuple[str, list[str]]] = []
    fenced = False
    for line in body.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            chunks.append((line[3:].strip(), []))
        elif chunks:
            chunks[-1][1].append(line)
        elif line.strip():
            raise ProcedureError(f"{where}: text before the first `## ` section")
    out: list[Section] = []
    for title, lines in chunks:
        key = _key_of(title)
        out.append(Section(key, title, "\n".join(lines).strip()))
    keys = [s.key for s in out]
    expected = list(SECTION_KEYS) + ([SITE_KEY] if allow_site and SITE_KEY in keys else [])
    if keys != expected:
        raise ProcedureError(
            f"{where}: sections must be, in this order: "
            f"{', '.join(_TITLES.get(k, 'At <vendor>') for k in SECTION_KEYS)}; got {[s.title for s in out]}"
        )
    empty = [s.title for s in out if not s.markdown]
    if empty:
        raise ProcedureError(f"{where}: empty section(s): {empty}")
    return tuple(out)


def _key_of(title: str) -> str:
    if title == SITE_TITLE:
        return SITE_KEY
    if title.startswith("At "):
        return "at"
    for key, wanted in _TITLES.items():
        if title == wanted:
            return key
    return f"?{title}"


def parse(text: str, where: str = "procedure", origin: str = "shipped") -> Procedure:
    """Parse and validate one procedure file. Raises `ProcedureError`."""
    data, body = _split_front(text, where)
    try:
        front = Front.model_validate(data)
    except ValidationError as exc:
        raise ProcedureError(f"{where}: {exc}") from exc
    proc = Procedure(front, _sections(body, where, allow_site=False), origin)
    problems = check(proc)
    if problems:
        raise ProcedureError(f"{where}: " + "; ".join(problems))
    return proc


def check(proc: Procedure) -> list[str]:
    """What is wrong with a parsed procedure that the shape alone does not say."""
    problems: list[str] = []
    allowed = {v.url for v in proc.front.vendor_docs}
    for section in proc.sections:
        for name, argument in placeholders_in(section.markdown):
            try:
                _check_placeholder(name, argument)
            except ValueError as exc:
                problems.append(f"{section.title}: {exc}")
        for target in links_in(section.markdown):
            if target not in allowed:
                problems.append(f"{section.title}: link {target} is not an https URL listed in vendor_docs")
        for shape in SECRET_SHAPES:
            if shape.search(section.markdown):
                problems.append(f"{section.title}: a secret-shaped string ({shape.pattern})")
    return problems


def _shipped() -> dict[str, Procedure]:
    out: dict[str, Procedure] = {}
    for path in sorted(SHIPPED.glob("*.md")):
        proc = parse(path.read_text(encoding="utf-8"), path.name)
        if proc.id != path.stem:
            raise ProcedureError(f"{path.name}: the id is `{proc.id}`; a file is named after its id")
        out[proc.id] = proc
    return out


def _directories(extra: str | os.PathLike[str] | None) -> Iterable[tuple[str, pathlib.Path]]:
    if extra:
        yield "extra", pathlib.Path(extra)
    for point in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        value = point.load()
        if not isinstance(value, str | os.PathLike):
            log.warning("procedures.plugin.refused", plugin=point.name, reason="a path is expected")
            continue
        yield f"plugin:{point.name}", pathlib.Path(value)


def _with_site_notes(base: Procedure, notes: str) -> Procedure:
    text = notes.strip()
    if text.startswith("## "):
        text = text.split("\n", 1)[1].strip() if "\n" in text else ""
    if not text:
        return base
    kept = tuple(s for s in base.sections if s.key != SITE_KEY)
    return Procedure(base.front, (*kept, Section(SITE_KEY, SITE_TITLE, text)), base.origin)


def _add_directory(cat: Catalogue, origin: str, directory: pathlib.Path) -> None:
    if not directory.is_dir():
        cat.problems.append(f"{origin}: {directory} is not a directory")
        return
    for path in sorted(directory.glob("*.md")):
        name = path.name
        try:
            text = path.read_text(encoding="utf-8")
            if name.endswith(".site.md"):
                target_id = name[: -len(".site.md")]
                base = cat.procedures.get(target_id)
                if base is None:
                    raise ProcedureError(f"{name}: site notes for `{target_id}`, which is not a procedure")
                updated = _with_site_notes(base, text)
                problems = check(updated)
                if problems:
                    raise ProcedureError(f"{name}: " + "; ".join(problems))
                cat.procedures[target_id] = updated
            else:
                proc = parse(text, name, origin)
                if proc.id != path.stem:
                    raise ProcedureError(f"{name}: the id is `{proc.id}`; a file is named after its id")
                if proc.id in cat.procedures:
                    raise ProcedureError(
                        f"{name}: `{proc.id}` already exists; add notes with `{proc.id}.site.md`"
                    )
                cat.procedures[proc.id] = proc
        except (ProcedureError, UnicodeDecodeError) as exc:
            cat.problems.append(str(exc))
            log.warning("procedures.file.refused", origin=origin, file=name, reason=str(exc))


@lru_cache(maxsize=8)
def _load(extra: str | None) -> Catalogue:
    cat = Catalogue(_shipped())
    for origin, directory in _directories(extra):
        _add_directory(cat, origin, directory)
    return cat


def load(extra: str | os.PathLike[str] | None = None) -> Catalogue:
    """The shipped procedures, then a deployment's additions. Cached per directory."""
    return _load(str(extra) if extra else None)


def reload() -> None:
    """Forget what was loaded (tests; the plugins' entry points are read again)."""
    _load.cache_clear()
