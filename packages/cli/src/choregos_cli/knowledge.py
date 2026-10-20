# SPDX-License-Identifier: Apache-2.0
"""A knowledge base as code (S39-18c): `choregos knowledge apply -f kb.yaml`.

The manifest declares one base, its sources, its uploaded files and its eval set. `apply` compares it
with what the API holds and does the difference, nothing more: run twice, the second run changes
nothing. It talks to the existing endpoints only (`/orgs/{org}/knowledge/...`).
"""

from __future__ import annotations

import glob
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlsplit

import httpx
import typer
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from rich.console import Console

from .client import ApiError, Client

knowledge_app = typer.Typer(help="Knowledge bases as code")
console = Console()

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


class ManifestError(ValueError):
    """The manifest is wrong; nothing was called yet."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceSpec(_Strict):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["web", "git", "sharepoint"]
    #: the organisation's `documents` connector (SharePoint; optional for git)
    connector: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    interval_minutes: int = Field(default=60, ge=5, le=7 * 24 * 60)

    @model_validator(mode="after")
    def _complete(self) -> SourceSpec:
        if self.kind in {"web", "git"} and not str(self.config.get("url") or "").startswith("https://"):
            raise ValueError(f"source `{self.name}`: a {self.kind} source needs an https `config.url`")
        if self.kind == "sharepoint" and not self.connector:
            raise ValueError(f"source `{self.name}`: a SharePoint source names its `connector`")
        return self


class UploadUrl(_Strict):
    url: str
    #: the file name in the base; the last segment of the URL by default
    name: str | None = None

    @model_validator(mode="after")
    def _https(self) -> UploadUrl:
        if not self.url.startswith("https://"):
            raise ValueError(f"an uploaded URL is https: `{self.url}`")
        return self


class EvalCase(_Strict):
    question: str = Field(min_length=1)
    expected: list[str] = Field(min_length=1)


class Manifest(_Strict):
    apiVersion: Literal["choregos/v1"]  # noqa: N815
    kind: Literal["KnowledgeBase"]
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    scope: Literal["org", "projects", "groups"] = "org"
    projects: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    serving_mode: Literal["files", "index"] = "files"
    sources: list[SourceSpec] = Field(default_factory=list)
    uploads: list[str | UploadUrl] = Field(default_factory=list)
    eval: list[EvalCase] | None = None

    @model_validator(mode="after")
    def _unique_sources(self) -> Manifest:
        names = [s.name for s in self.sources]
        if len(set(names)) != len(names):
            raise ValueError("two sources share a name")
        return self


def load_manifest(path: Path) -> Manifest:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"cannot read `{path}`: {exc}") from exc
    try:
        return Manifest.model_validate(data)
    except ValidationError as exc:
        lines = [f"  - {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise ManifestError(f"`{path}` is not a valid knowledge base manifest:\n" + "\n".join(lines)) from exc


@dataclass
class Upload:
    filename: str
    data: bytes

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.data).hexdigest()


def _download(url: str, transport: httpx.BaseTransport | None = None) -> bytes:
    """https only, at most 20 MiB, redirects followed only to https."""
    http = httpx.Client(timeout=60.0, follow_redirects=True, transport=transport)
    with http, http.stream("GET", url) as response:
        if response.url.scheme != "https":
            raise ManifestError(f"`{url}` redirected to a non-https URL")
        if response.status_code != 200:
            raise ManifestError(f"`{url}` answered {response.status_code}")
        data = bytearray()
        for chunk in response.iter_bytes():
            data += chunk
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise ManifestError(f"`{url}` is larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MiB")
    return bytes(data)


def collect_uploads(
    manifest: Manifest, base_dir: Path, transport: httpx.BaseTransport | None = None
) -> list[Upload]:
    """The files the manifest declares: local globs (relative to the manifest) and https URLs."""
    found: dict[str, Upload] = {}

    def add(upload: Upload) -> None:
        if upload.filename in found:
            raise ManifestError(f"two uploads are named `{upload.filename}`")
        found[upload.filename] = upload

    for entry in manifest.uploads:
        if isinstance(entry, UploadUrl):
            name = entry.name or unquote(Path(urlsplit(entry.url).path).name)
            if not name:
                raise ManifestError(f"cannot name the download `{entry.url}`: give it a `name`")
            add(Upload(name, _download(entry.url, transport)))
            continue
        matches = sorted(p for p in glob.glob(str(base_dir / entry), recursive=True) if Path(p).is_file())
        if not matches:
            raise ManifestError(f"the upload pattern `{entry}` matches no file (relative to the manifest)")
        for match in matches:
            add(Upload(Path(match).name, Path(match).read_bytes()))
    return list(found.values())


@dataclass
class Step:
    symbol: str  # + create, ~ change, - remove, = unchanged, ! left alone
    text: str
    run: Callable[[], None] | None = None


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)

    def add(self, symbol: str, text: str, run: Callable[[], None] | None = None) -> None:
        self.steps.append(Step(symbol, text, run))

    @property
    def changes(self) -> list[Step]:
        return [s for s in self.steps if s.symbol not in {"=", "!"}]


def _norm(value: Any) -> Any:
    return None if value in (None, [], "", {}) else value


def _config_differs(desired: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Only the keys the manifest names count: the API adds defaults and clamps some values."""
    return any(_norm(actual.get(k)) != _norm(v) for k, v in desired.items())


def _plan_base(
    plan: Plan, api: Client, root: str, manifest: Manifest, current: dict[str, Any] | None
) -> None:
    base_path = f"{root}/{manifest.slug}"
    wanted = {
        "name": manifest.name,
        "description": manifest.description,
        "scope": manifest.scope,
        "projects": manifest.projects,
        "groups": manifest.groups,
        "serving_mode": manifest.serving_mode,
    }
    if current is None:
        body = {"slug": manifest.slug, **wanted}
        plan.add("+", f"knowledge base {manifest.slug}", lambda: api.post(root, json=body))
        return
    diff = {k: v for k, v in wanted.items() if _norm(current.get(k)) != _norm(v)}
    if diff:
        plan.add(
            "~",
            f"knowledge base {manifest.slug} ({', '.join(sorted(diff))})",
            lambda: api.request("PATCH", base_path, json=diff),
        )
    else:
        plan.add("=", f"knowledge base {manifest.slug}")


def _plan_sources(
    plan: Plan, api: Client, base_path: str, manifest: Manifest, current: dict[str, Any], prune: bool
) -> None:
    sources = {s["name"]: s for s in current.get("sources") or []}
    for spec in manifest.sources:
        body = {
            "kind": spec.kind,
            "name": spec.name,
            "connector": spec.connector,
            "config": spec.config,
            "interval_minutes": spec.interval_minutes,
        }
        existing = sources.pop(spec.name, None)
        if existing is None:
            plan.add("+", f"source {spec.name}", partial(api.post, f"{base_path}/sources", json=body))
        elif existing["kind"] != spec.kind or _norm(existing.get("connector")) != _norm(spec.connector):

            def recreate(sid: str = existing["id"], b: dict[str, Any] = body) -> None:
                api.delete(f"{base_path}/sources/{sid}")
                api.post(f"{base_path}/sources", json=b)

            plan.add("~", f"source {spec.name} (kind or connector changed: recreated)", recreate)
        elif (
            _config_differs(spec.config, existing.get("config") or {})
            or existing["interval_minutes"] != spec.interval_minutes
        ):
            patch = {"config": spec.config, "interval_minutes": spec.interval_minutes}
            url = f"{base_path}/sources/{existing['id']}"
            plan.add("~", f"source {spec.name}", partial(api.request, "PATCH", url, json=patch))
        else:
            plan.add("=", f"source {spec.name}")
    for name, extra in sources.items():
        if prune:
            url = f"{base_path}/sources/{extra['id']}"
            plan.add("-", f"source {name}", partial(api.delete, url))
        else:
            plan.add("!", f"source {name} (not in the manifest, kept: use --prune)")


def _plan_uploads(
    plan: Plan, api: Client, base_path: str, uploads: list[Upload], current: dict[str, Any], prune: bool
) -> None:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for doc in current.get("documents") or []:
        if doc.get("source") == "upload":
            by_name.setdefault(doc["filename"], []).append(doc)
    for upload in uploads:
        docs = by_name.pop(upload.filename, [])
        if any(d["digest"] == upload.digest for d in docs):
            plan.add("=", f"upload {upload.filename}")
            for stale in (d for d in docs if d["digest"] != upload.digest):
                url = f"{base_path}/documents/{stale['id']}"
                plan.add("-", f"upload {upload.filename} (older version)", partial(api.delete, url))
            continue

        def replace(up: Upload = upload, old: list[str] = [d["id"] for d in docs]) -> None:  # noqa: B006
            for document_id in old:
                api.delete(f"{base_path}/documents/{document_id}")
            api.post(f"{base_path}/documents", files={"file": (up.filename, up.data)})

        plan.add("~" if docs else "+", f"upload {upload.filename}", replace)
    for name, docs in by_name.items():
        if not prune:
            plan.add("!", f"upload {name} (not in the manifest, kept: use --prune)")
            continue
        for doc in docs:
            url = f"{base_path}/documents/{doc['id']}"
            plan.add("-", f"upload {name}", partial(api.delete, url))


def _plan_eval(plan: Plan, api: Client, base_path: str, manifest: Manifest, current: dict[str, Any]) -> None:
    if manifest.eval is None:
        return
    cases = [c.model_dump() for c in manifest.eval]
    have = [{"question": c["question"], "expected": c["expected"]} for c in current.get("eval_set") or []]
    label = f"eval set ({len(cases)} questions)"
    if have == cases:
        plan.add("=", label)
    else:
        plan.add("~" if have else "+", label, lambda: api.put(f"{base_path}/eval", json={"cases": cases}))


def build_plan(api: Client, org: str, manifest: Manifest, uploads: list[Upload], *, prune: bool) -> Plan:
    """Reads the base and plans the difference; calls nothing that changes it."""
    plan = Plan()
    root = f"/orgs/{org}/knowledge"
    base_path = f"{root}/{manifest.slug}"
    try:
        current: dict[str, Any] | None = api.get(base_path)
    except ApiError as exc:
        if exc.status_code != 404:
            raise
        current = None
    _plan_base(plan, api, root, manifest, current)
    known = current or {"sources": [], "documents": [], "eval_set": []}
    _plan_sources(plan, api, base_path, manifest, known, prune)
    _plan_uploads(plan, api, base_path, uploads, known, prune)
    _plan_eval(plan, api, base_path, manifest, known)
    return plan


def print_plan(plan: Plan) -> None:
    colours = {"+": "green", "~": "yellow", "-": "red", "=": "dim", "!": "yellow"}
    unchanged = [s for s in plan.steps if s.symbol == "="]
    for step in (s for s in plan.steps if s.symbol != "="):
        console.print(f"[{colours[step.symbol]}]{step.symbol}[/{colours[step.symbol]}] {step.text}")
    console.print(f"= {len(unchanged)} unchanged" if unchanged else "= nothing unchanged")


def _org(org: str | None) -> str:
    from .client import Profile

    return org or Profile.load().org


def _client() -> Client:
    from .main import TransportSafeClient

    return TransportSafeClient()


def _fail(message: str) -> None:
    from .main import fail

    fail(message)


@knowledge_app.command("apply")
def apply(
    file: Annotated[Path, typer.Option("-f", "--file", help="The manifest (YAML).")],
    org: Annotated[str | None, typer.Option(help="The organisation (default: the profile's).")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the plan, change nothing.")] = False,
    prune: Annotated[
        bool,
        typer.Option("--prune", help="Also remove the sources and uploads the manifest does not declare."),
    ] = False,
) -> None:
    """Make a knowledge base match its manifest: create or patch the base, its sources, its uploads
    and its eval set. Idempotent: a second run changes nothing."""
    try:
        manifest = load_manifest(file)
        uploads = collect_uploads(manifest, file.resolve().parent)
    except ManifestError as exc:
        _fail(str(exc))
        return
    api = _client()
    try:
        plan = build_plan(api, _org(org), manifest, uploads, prune=prune)
        print_plan(plan)
        if dry_run:
            console.print("dry run: nothing changed")
            return
        for step in plan.changes:
            if step.run is not None:
                step.run()
        console.print(f"[green]✓[/green] {len(plan.changes)} change(s) applied")
    except ApiError as exc:
        _fail(str(exc))
    finally:
        api.close()


@knowledge_app.command("sync")
def sync(
    slug: str,
    org: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Sync every source of a base now."""
    api = _client()
    try:
        base = api.get(f"/orgs/{_org(org)}/knowledge/{slug}")
        for source in base.get("sources") or []:
            api.post(f"/orgs/{_org(org)}/knowledge/{slug}/sources/{source['id']}/sync")
            console.print(f"sync requested: {source['name']}")
        if not base.get("sources"):
            console.print("no source to sync")
    except ApiError as exc:
        _fail(str(exc))
    finally:
        api.close()


@knowledge_app.command("eval")
def eval_(
    slug: str,
    org: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Run the base's eval set against its index and print recall@5."""
    api = _client()
    try:
        base = api.post(f"/orgs/{_org(org)}/knowledge/{slug}/eval/run")
    except ApiError as exc:
        _fail(str(exc))
        return
    finally:
        api.close()
    result = base.get("eval_result") or {}
    console.print(f"recall@{result.get('k', 5)} = {result.get('recall')} (mode: {result.get('mode')})")
    for case in result.get("cases") or []:
        ok = len(case.get("found") or []) == len(case.get("expected") or [])
        console.print(f"  {'✓' if ok else '✗'} {case.get('question')}")
