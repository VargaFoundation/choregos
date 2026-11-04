# SPDX-License-Identifier: Apache-2.0
"""Tool catalogue: third-party APIs an agent can use, without giving it a key.

WHY THIS FILE EXISTS
--------------------
The HR demonstration of 2026-09-23 stopped on "no candidate profile database reachable". It was
not a defect of the engine: it was an absence of **data**. An agent that investigates a case,
looks up a profile or checks a company needs third-party APIs, and those APIs have keys.

The reflex would be to put the key in the agent's pod. AGENTS.md forbids it, and rightly: an agent
that carries an API key can spend it without a ceiling, exfiltrate it in a commit, or keep it. The
catalogue takes the problem from the other end, exactly as the LLM gateway does for models:

- the **deployment** declares the tools and holds the keys;
- the agent calls the tool **by its name**, with its run token as its only identity;
- the platform calls the provider, injects the key on the server side, and **counts the call**.

The agent never sees the key, never goes out to the provider (its egress stays closed), and its
spending is capped per run like everything else.

WHAT THIS IS NOT
----------------
Not a directory of thousands of tools. A catalogue is a **short, chosen** list, written by whoever
deploys, reviewed like code. The day one wants the breadth of a public directory, a catalogue tool
leads there, not a dependency of the platform.

The operators' YAML key is `tools:`; the former `outils:` is still read until 0.23.0 (ADR 0098 §3 (f)).
"""

from __future__ import annotations

import logging
import posixpath
import re
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from .errors import ChoregosError

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

_logger = logging.getLogger("choregos.catalogue")
_former_key_warned: set[str] = set()  # the key is warned about once per process


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HttpCall(Model):
    """How the call goes to the provider.

    The `{{ field }}` placeholders are filled with the agent's arguments, and **only** with them:
    no free URL, no free method. A catalogue tool cannot be used to reach just anything,
    otherwise the catalogue would become an open proxy in the platform's name.
    """

    method: Literal["GET", "POST"] = "GET"
    url: str
    query: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = Field(default=20.0, gt=0, le=120)


class McpCall(Model):
    """A tool served by an MCP server **outside the organisation**.

    THE RUN'S TOKEN NEVER LEAVES. It authenticates the agent to US, and nothing else: handing it
    to a remote service would give it what it takes to write into the platform: post a result,
    file a finding, ask for a wider perimeter. The remote server receives `credential_env`, a key
    that is worth something only to it.

    `tool` is the tool's name AT THE REMOTE SERVER; the name exposed to the agent is the
    catalogue's. This indirection is not cosmetic: it makes it possible to expose only part of a
    server's tools, and to rename them in the house vocabulary.
    """

    url: str
    #: The tool as the remote server names it.
    tool: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = Field(default=30.0, gt=0, le=120)


class CatalogueTool(Model):
    """A tool: what the agent sees, and what the platform does for it."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    description: str
    provider: str
    categories: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    #: Groups of the organisation allowed to use it. **Empty = no group restriction**: the tool
    #: stays subject to the project's declaration (`ProjectConfig.tools`). Two locks, and they do
    #: not say the same thing: the deployment says WHO may (groups), the project says what IT uses
    #: (list of tools). A project that declares a tool reserved to a group it does not belong to
    #: does not get it.
    groups: list[str] = Field(default_factory=list)
    #: One OR the other: a direct HTTP call, or a tool of a remote MCP server.
    http: HttpCall | None = None
    mcp: McpCall | None = None
    #: Name of the environment variable that carries the provider's key, on the platform side.
    #: Never the key itself: this file is read in a PR.
    credential_env: str | None = None
    #: What a call costs, in euros. Zero for a free API, but count it all the same, because a
    #: free quota runs out too.
    price_eur: float = Field(default=0.0, ge=0)
    #: The most one of its answers may weigh, as JSON bytes, when it reaches the agent; none: the
    #: platform's default. The platform's ceiling still applies (S40-17).
    result_max_bytes: int | None = Field(default=None, ge=1024)
    #: The deployment's statement that the tool only reads. Unset, a GET http tool reads, while a
    #: POST or a remote MCP tool may write (S47-12, ADR 0034 §7).
    read_only: bool | None = None

    @model_validator(mode="after")
    def _a_single_source(self) -> CatalogueTool:
        """A tool has ONE source: an HTTP call, or a remote MCP tool.

        With neither the tool does nothing; with both one would not know which to use. Saying so
        when the catalogue loads beats saying so at the first call.
        """
        if (self.http is None) == (self.mcp is None):
            raise ValueError(f"the tool {self.name} must declare `http:` or `mcp:`, and only one")
        return self

    @property
    def may_write(self) -> bool:
        """May a call to this tool write to a third party? Catalogue calls are not governed actions."""
        if self.read_only is not None:
            return not self.read_only
        return self.mcp is not None or (self.http is not None and self.http.method != "GET")

    def open_to(self, groups: list[str]) -> bool:
        """Is the tool open to these groups? With no declared restriction, yes."""
        return not self.groups or bool(set(self.groups) & set(groups))


class Catalogue(Model):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    #: The YAML key is `tools:`; the former `outils:` is read until 0.23.0 (ADR 0098 §3 (f)).
    tools: list[CatalogueTool] = Field(
        default_factory=list,
        validation_alias=AliasChoices("tools", "outils"),  # lang: fr-ok alias:external
    )

    @model_validator(mode="before")
    @classmethod
    def _warn_about_the_former_key(cls, data: Any) -> Any:
        if isinstance(data, dict) and "outils" in data:  # lang: fr-ok alias:external
            if "tools" in data:
                raise ValueError("a catalogue declares `tools:` or the former `outils:`, not both")
            if not _former_key_warned:
                _former_key_warned.add("outils")  # lang: fr-ok alias:external
                _logger.warning(
                    "the catalogue key `outils:` is deprecated: write `tools:` "  # lang: fr-ok alias:external
                    "(the former key is removed in 0.23.0)"
                )
        return data

    def by_name(self, name: str) -> CatalogueTool | None:
        return next((t for t in self.tools if t.name == name), None)


class UnknownToolError(ChoregosError):
    """A name absent from the catalogue, or refused to the project."""


class ArgumentRefusedError(ChoregosError):
    """An argument is missing, or does not respect the schema the tool announces."""


def validate_arguments(tool: CatalogueTool, arguments: dict[str, Any]) -> None:
    """Refuse arguments that do not respect `input_schema`.

    `input_schema` was **decorative**: the platform announced it to the agent (it is the
    `inputSchema` that `GET /internal/runs/{id}/tools` returns in MCP format) and never checked it.
    An agent therefore had every reason to believe it binding, and it was not. A schema that is
    announced and not enforced is worse than no schema: it describes a guarantee that does not
    exist.

    A tool without a schema imposes nothing, and that is deliberate: the catalogue stays writable
    by hand for a trivial tool.
    """
    if not tool.input_schema:
        return
    import jsonschema

    try:
        jsonschema.validate(arguments, tool.input_schema)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ArgumentRefusedError(
            f"argument refused by the schema of {tool.name} at {path}: {exc.message}"
        ) from exc


def _fill(template: str, arguments: dict[str, Any], *, encode: bool = False) -> str:
    """Fill `{{ key }}`. `encode` percent-encodes the value: to use inside a URL."""

    def _one(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in arguments:
            raise ArgumentRefusedError(f"missing argument: {key}")
        value = str(arguments[key])
        return quote(value, safe="") if encode else value

    return PLACEHOLDER.sub(_one, template)


def _url_of(template: str, arguments: dict[str, Any]) -> str:
    """The URL of a call: the agent's values fill it **without being able to move it**.

    The catalogue exists precisely so that the agent does not choose the URL (ADR 0014). It
    partly did: the value was substituted bare, so `{{ owner }}` set to `../..` climbed the path,
    and a tool meant to read `/repos/<org>/<repository>` reached any other entry point of the same
    host, **with the provider's key that the platform attaches there**.

    Two guards, because the first is not enough: encoding neutralises `/`, `?` and `#`, but **not**
    the climb: `quote("..")` gives `..`, the dot being an unreserved character. The second
    therefore compares the NORMALISED URL with the template's literal prefix, the one before the
    first `{{`. What leaves that prefix is refused, however it is written.
    """
    url = _fill(template, arguments, encode=True)
    prefix = template.split("{{", 1)[0]
    if prefix == template:  # no placeholder: nothing to check
        return url
    split, expected = urlsplit(url), urlsplit(prefix)
    if (split.scheme, split.netloc) != (expected.scheme, expected.netloc):
        raise ArgumentRefusedError(f"an argument moved the call's host: {url}")
    normalised = posixpath.normpath(split.path)
    if split.path.endswith("/") and not normalised.endswith("/"):
        normalised += "/"
    if not normalised.startswith(expected.path):
        raise ArgumentRefusedError(
            f"an argument left the tool's path: {normalised} is not under {expected.path}"
        )
    return urlunsplit((split.scheme, split.netloc, normalised, split.query, split.fragment))


def build_request(tool: CatalogueTool, arguments: dict[str, Any], key: str | None) -> dict[str, Any]:
    """Render what it takes to call the provider: method, URL, parameters, headers, body.

    The key appears only in the headers rendered here, never in what is logged nor in what goes
    back to the agent.
    """
    if tool.http is None:
        raise ValueError(f"the tool {tool.name} has no HTTP source")
    headers = {
        name: _fill(value, {**arguments, "credential": key or ""})
        for name, value in tool.http.headers.items()
    }
    return {
        "method": tool.http.method,
        "url": _url_of(tool.http.url, arguments),
        "params": {name: _fill(v, arguments) for name, v in tool.http.query.items()},
        "headers": headers,
        "json": _fill_deep(tool.http.body, arguments) if tool.http.body else None,
        "timeout": tool.http.timeout_s,
    }


def _fill_deep(value: Any, arguments: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _fill(value, arguments)
    if isinstance(value, dict):
        return {k: _fill_deep(v, arguments) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_deep(v, arguments) for v in value]
    return value


def load_catalogue(text: str) -> Catalogue:
    """Read a YAML catalogue. An unreadable catalogue is an error, not an empty catalogue."""
    import yaml

    data = yaml.safe_load(text) or {}
    if isinstance(data, list):
        data = {"tools": data}
    return Catalogue.model_validate(data)


__all__ = [
    "ArgumentRefusedError",
    "Catalogue",
    "CatalogueTool",
    "HttpCall",
    "McpCall",
    "UnknownToolError",
    "build_request",
    "load_catalogue",
    "validate_arguments",
]
