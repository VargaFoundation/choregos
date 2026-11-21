# SPDX-License-Identifier: Apache-2.0
"""`choregos artifacts`: a work item's artefacts, the person's inbox and a run's diff (S67-11, ADR 0154 §2).

List, download to a path, open with the system's viewer, compare two revisions of a text artefact. Everything
reads routes that exist: `GET /work-items/{id}/artifacts`, `…/{ref}`, `…/{ref}/content`, `GET /me/inbox` and
`GET /runs/{id}/diff`. A revision is addressed as `name` (the latest) or `name@version` (ADR 0049), so
comparing two versions needs no route of its own: it fetches the two refs and diffs them here.

The functions above `artifacts_app` are the logic and import no UI toolkit: the `chat` workspace's "elsewhere"
pane (S67-09) calls them and draws the result itself. They raise `ArtifactError` for a refusal; the commands
turn it into a red line and exit code 1.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import theme
from .client import ApiError, Client

DIGEST_HEADER = "X-Choregos-Digest"
#: Media types, beyond `text/*`, that are text and so can be compared line by line.
TEXT_MEDIA_TYPES = frozenset(
    {"application/json", "application/xml", "application/yaml", "application/x-yaml", "application/toml"}
)
#: Media-type suffixes (`application/ld+json`) that are text.
TEXT_SUFFIXES = ("+json", "+xml", "+yaml")


class ArtifactError(RuntimeError):
    """A refusal a person can act on: the file exists, the digest does not match, not text."""


# ───────────────────────────── reading ─────────────────────────────


def list_artifacts(api: Client, work_item: str) -> list[dict[str, Any]]:
    """Every revision of every artefact of the work item, by name then version (`Artifact`)."""
    return list(api.get_pages(f"/work-items/{work_item}/artifacts"))


def inbox(api: Client, limit: int = 100) -> list[dict[str, Any]]:
    """The artefacts the person's tasks delivered, newest first (`InboxItem`)."""
    listing = api.get("/me/inbox", params={"limit": limit}) or {}
    return list(listing.get("items") or [])


def run_diff(api: Client, run_id: str) -> dict[str, Any]:
    """What a run changed (`DiffSummary`)."""
    return dict(api.get(f"/runs/{run_id}/diff") or {})


@dataclass(frozen=True)
class Content:
    """The bytes the API served for one revision, and what it said about them."""

    ref: str
    data: bytes
    media_type: str
    digest: str | None

    @property
    def is_text(self) -> bool:
        return is_text_media(self.media_type)


def is_text_media(media_type: str) -> bool:
    base = media_type.split(";", maxsplit=1)[0].strip().lower()
    return base.startswith("text/") or base in TEXT_MEDIA_TYPES or base.endswith(TEXT_SUFFIXES)


def fetch(api: Client, work_item: str, ref: str) -> Content:
    """The bytes of `name` or `name@version`, as served. They are checked against the digest the API sent
    with them: bytes that do not match are never handed on (the API refuses them too, with a 409)."""
    response = api.get_raw(f"/work-items/{work_item}/artifacts/{ref}/content", params={"download": "true"})
    data = response.content
    digest = response.headers.get(DIGEST_HEADER)
    if digest:
        actual = "sha256:" + hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise ArtifactError(f"{ref}: the bytes received are {actual}, the API announced {digest}")
    return Content(ref, data, response.headers.get("content-type", "application/octet-stream"), digest)


# ───────────────────────────── writing ─────────────────────────────


def default_name(ref: str) -> str:
    """The file an artefact is saved as: its name, without the `@version`."""
    return ref.split("@", 1)[0]


def save(content: Content, target: Path, *, force: bool = False) -> Path:
    """Write exactly the bytes served. A directory receives the artefact's name; an existing file is kept
    unless `force`. The file is created exclusively, so two writers cannot both pass the check."""
    path = target / default_name(content.ref) if target.is_dir() else target
    try:
        with path.open("wb" if force else "xb") as handle:
            handle.write(content.data)
    except FileExistsError:
        raise ArtifactError(f"{path} exists: nothing written (--force overwrites it)") from None
    except OSError as exc:
        raise ArtifactError(f"cannot write {path}: {exc.strerror or exc}") from None
    return path


# ───────────────────────────── opening ─────────────────────────────

Opener = Callable[[Path], None]


def is_wsl(environ: Mapping[str, str] | None = None, release: str | None = None) -> bool:
    env = os.environ if environ is None else environ
    text = platform.release() if release is None else release
    return bool(env.get("WSL_DISTRO_NAME")) or "microsoft" in text.lower()


def viewer_command(
    path: Path,
    system: str,
    *,
    wsl: bool = False,
    which: Callable[[str], str | None] = shutil.which,
    to_windows: Callable[[Path], str] | None = None,
) -> list[str]:
    """The command that opens `path` with the platform's own opener: `open` on macOS, `explorer` on Windows,
    `wslview` (or `explorer.exe` with a Windows path) on WSL, `xdg-open` elsewhere."""
    if system == "Darwin":
        return ["open", str(path)]
    if system == "Windows":
        return ["explorer", str(path)]
    if wsl:
        if which("wslview"):
            return ["wslview", str(path)]
        converted = to_windows(path) if to_windows else str(path)
        return ["explorer.exe", converted]
    return ["xdg-open", str(path)]


def _wslpath(path: Path) -> str:
    command = ["wslpath", "-w", str(path)]
    done = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    return done.stdout.strip() or str(path)


def system_opener(path: Path) -> None:
    """The default opener: the platform's viewer, started and not waited for."""
    command = viewer_command(path, platform.system(), wsl=is_wsl(), to_windows=_wslpath)
    try:
        subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise ArtifactError(f"cannot start {command[0]}: {exc.strerror or exc}") from None


def open_in_viewer(content: Content, opener: Opener | None = None) -> Path:
    """Write a private temporary copy under the artefact's name and hand it to the opener. The copy is left
    in place: the viewer reads it after this returns."""
    folder = Path(tempfile.mkdtemp(prefix="choregos-"))
    path = folder / default_name(content.ref)
    path.write_bytes(content.data)
    path.chmod(0o600)
    (opener or system_opener)(path)
    return path


# ───────────────────────────── comparing ─────────────────────────────


def unified_diff(before: Content, after: Content, *, context: int = 3) -> list[str]:
    """The unified diff of two text revisions, as lines without their newline; empty when identical."""
    for side in (before, after):
        if not side.is_text:
            raise ArtifactError(
                f"{side.ref} is {side.media_type}, not text: only text artefacts are compared"
            )
    try:
        old = before.data.decode("utf-8").splitlines()
        new = after.data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise ArtifactError("an artefact declared as text is not valid UTF-8: not compared") from None
    return list(difflib.unified_diff(old, new, before.ref, after.ref, n=context, lineterm=""))


def diff_line(line: str) -> Text:
    """One line of a unified diff in its colour; the `+`/`-`/`@@` marks say the same without it."""
    if line.startswith(("+++", "---")):
        return Text(line, style="bold")
    if line.startswith("@@"):
        return Text(line, style="cyan")
    if line.startswith("+"):
        return Text(line, style="green")
    if line.startswith("-"):
        return Text(line, style="red")
    return Text(line)


def render_diff(lines: list[str], out: Console) -> None:
    for line in lines:
        out.print(diff_line(line), soft_wrap=True)


def artifacts_table(rows: list[dict[str, Any]]) -> Table:
    table = Table("name", "version", "type", "size", "run", "digest")
    for row in rows:
        produced = row.get("produced_by") or {}
        table.add_row(
            row["name"],
            str(row["version"]),
            row["type"],
            str(row["size"]),
            str(produced.get("run_id", "")),
            str(row["digest"])[:19],
        )
    return table


def inbox_table(rows: list[dict[str, Any]]) -> Table:
    table = Table("name", "version", "type", "work item", "task", "delivered")
    for row in rows:
        table.add_row(
            row["name"],
            str(row["version"]),
            row["type"],
            row["work_item_id"],
            row["task"],
            row["created_at"],
        )
    return table


def render_run_diff(summary: dict[str, Any], out: Console) -> None:
    out.print(f"{len(summary.get('files') or [])} file(s) · +{summary['additions']} -{summary['deletions']}")
    for entry in summary.get("files") or []:
        scope = "" if entry.get("in_scope", True) else "  (out of scope)"
        out.print(
            Text(f"{entry['status']:<9} {entry['path']}  +{entry['additions']} -{entry['deletions']}{scope}")
        )
        if entry.get("patch"):
            render_diff(str(entry["patch"]).splitlines(), out)


# ───────────────────────────── commands ─────────────────────────────

artifacts_app = typer.Typer(
    help="A work item's artefacts, your inbox and a run's diff: list, download, open, compare.",
    no_args_is_help=True,
)


def _api() -> Client:
    from . import main

    return main.TransportSafeClient()


def _fail(message: str) -> None:
    from . import main

    main.fail(message)


def _json(payload: Any) -> None:
    import json

    sys.stdout.write(json.dumps(payload, indent=2) + "\n")


@artifacts_app.command("list")
def list_command(
    work_item: Annotated[str, typer.Argument(help="the work item's id")],
    as_json: Annotated[bool, typer.Option("--json", help="print the API's list as JSON")] = False,
) -> None:
    """Every revision of every artefact of a work item."""
    try:
        rows = list_artifacts(_api(), work_item)
    except ApiError as exc:
        _fail(str(exc))
        return
    if as_json:
        _json(rows)
    elif not rows:
        theme.console().print("no artefacts yet")
    else:
        theme.console().print(artifacts_table(rows))


@artifacts_app.command("inbox")
def inbox_command(
    limit: Annotated[int, typer.Option(min=1, max=200, help="how many, newest first")] = 100,
    as_json: Annotated[bool, typer.Option("--json", help="print the API's list as JSON")] = False,
) -> None:
    """The artefacts your tasks delivered, newest first."""
    try:
        rows = inbox(_api(), limit)
    except ApiError as exc:
        _fail(str(exc))
        return
    if as_json:
        _json(rows)
    elif not rows:
        theme.console().print("your inbox is empty")
    else:
        theme.console().print(inbox_table(rows))


@artifacts_app.command("download")
def download_command(
    work_item: Annotated[str, typer.Argument(help="the work item's id")],
    ref: Annotated[str, typer.Argument(help="`name` for the latest revision, `name@version` for one")],
    path: Annotated[
        Path | None, typer.Argument(help="where to write; a directory, or the artefact's name")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="overwrite an existing file")] = False,
) -> None:
    """Save an artefact's bytes, exactly as the API served them. An existing file is kept without --force."""
    target = path or Path.cwd()
    try:
        written = save(fetch(_api(), work_item, ref), target, force=force)
    except (ApiError, ArtifactError) as exc:
        _fail(str(exc))
        return
    theme.console(stderr=True).print(f"wrote {written}")


@artifacts_app.command("open")
def open_command(
    work_item: Annotated[str, typer.Argument(help="the work item's id")],
    ref: Annotated[str, typer.Argument(help="`name` for the latest revision, `name@version` for one")],
) -> None:
    """Open an artefact with the system's viewer, from a temporary copy."""
    try:
        path = open_in_viewer(fetch(_api(), work_item, ref))
    except (ApiError, ArtifactError) as exc:
        _fail(str(exc))
        return
    theme.console(stderr=True).print(f"opened {path}")


@artifacts_app.command("compare")
def compare_command(
    work_item: Annotated[str, typer.Argument(help="the work item's id")],
    before: Annotated[str, typer.Argument(help="`name@version`")],
    after: Annotated[str, typer.Argument(help="`name@version`, or `name` for the latest")],
) -> None:
    """A unified diff of two revisions of a text artefact (two refs: the API has no diff route)."""
    api = _api()
    try:
        lines = unified_diff(fetch(api, work_item, before), fetch(api, work_item, after))
    except (ApiError, ArtifactError) as exc:
        _fail(str(exc))
        return
    out = theme.console()
    if lines:
        render_diff(lines, out)
    else:
        out.print(f"{before} and {after} are identical")


@artifacts_app.command("diff")
def diff_command(
    run_id: Annotated[str, typer.Argument(help="the run's id")],
    as_json: Annotated[bool, typer.Option("--json", help="print the API's summary as JSON")] = False,
) -> None:
    """What a run changed: its files and their patches."""
    try:
        summary = run_diff(_api(), run_id)
    except ApiError as exc:
        _fail(str(exc))
        return
    if as_json:
        _json(summary)
    else:
        render_run_diff(summary, theme.console())
