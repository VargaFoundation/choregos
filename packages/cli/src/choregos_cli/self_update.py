# SPDX-License-Identifier: Apache-2.0
"""The CLI keeps in step with its instance (S62-08).

- `choregos self update`: downloads, from the instance the profile points at, the native binary
  (S62-07) of the version that instance runs for this OS and processor, checks its sha256 against
  the instance's manifest, and puts it in place of the running one, atomically. On Windows a
  running `.exe` cannot be overwritten but can be renamed: the old one steps aside (`.old`), the
  new one takes its name, and the leftover is removed at the next update.
- The version check: when the CLI talks to an instance whose version (`GET /api/v1/edition`, public)
  differs from its own, it writes ONE line on stderr saying how to update, at most once a day per
  instance. `CHOREGOS_NO_VERSION_CHECK=1` silences it. It never fails a command: any error is
  swallowed, and the request it makes has a short timeout.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from choregos_core.publishing import PUBLISHING
from rich.console import Console

RELEASES = PUBLISHING.releases
#: The variable that silences the version check.
NO_CHECK_ENV = "CHOREGOS_NO_VERSION_CHECK"
#: Seconds between two checks of one instance (and so between two warnings).
CHECK_EVERY_S = 24 * 3600
CHECK_TIMEOUT_S = 2.0

_OS = {"linux": "linux", "darwin": "macos", "windows": "windows"}
_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}


class UpdateError(RuntimeError):
    """A readable reason the update did not happen; nothing was replaced."""


def cli_version() -> str:
    try:
        return metadata.version("choregos-cli")
    except metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def known(version: str) -> bool:
    """A version worth comparing: a development tree says `0.0.0+unknown`."""
    return bool(version) and not version.startswith("0.0.0")


def this_platform() -> tuple[str, str]:
    """(`linux|macos|windows`, `x86_64|arm64`), the manifest's words; UpdateError otherwise."""
    system = _OS.get(platform.system().lower())
    arch = _ARCH.get(platform.machine().lower())
    if system is None or arch is None:
        raise UpdateError(f"no choregos binary is built for {platform.system()}/{platform.machine()}")
    return system, arch


def running_binary() -> Path | None:
    """The native binary this process runs from, or None when it runs from Python packages.

    Nuitka (S62-07) defines `__compiled__` in every module it compiles; in onefile mode
    `sys.argv[0]` is the onefile binary as it was called, which a shell may give as a bare name.
    """
    if "__compiled__" not in globals() and not getattr(sys, "frozen", False):
        return None
    called = sys.argv[0]
    if os.sep not in called and (os.altsep is None or os.altsep not in called):
        called = shutil.which(called) or called
    return Path(called).resolve()


def install_hint(api_url: str, system: str | None = None) -> str:
    """The one-line reinstall from the instance, for this OS."""
    base = api_url.rstrip("/")
    if (system or _OS.get(platform.system().lower())) == "windows":
        return f"irm {base}/cli/install.ps1 | iex"
    return f"curl -fsSL {base}/cli/install.sh | sh"


@dataclass
class Manifest:
    version: str
    files: list[dict[str, Any]]

    def entry(self, system: str, arch: str) -> dict[str, Any]:
        for candidate in self.files:
            if candidate.get("os") == system and candidate.get("arch") == arch:
                return candidate
        raise UpdateError(f"the instance's CLI {self.version} has no binary for {system}/{arch}")


def fetch_manifest(http: httpx.Client, api_url: str) -> Manifest:
    url = f"{api_url.rstrip('/')}/downloads/cli/manifest.json"
    try:
        response = http.get(url)
    except httpx.TransportError as exc:
        raise UpdateError(f"cannot reach {url}: {exc or type(exc).__name__}") from None
    if response.status_code == 404:
        try:
            problem = response.json()
        except ValueError:
            problem = {}
        release = problem.get("release_url") or RELEASES
        raise UpdateError(f"the instance at {api_url} bundles no CLI; download it from {release}")
    if response.status_code != 200:
        raise UpdateError(f"{url} answered {response.status_code}")
    try:
        body = response.json()
        return Manifest(version=str(body["version"]), files=list(body["files"]))
    except (ValueError, KeyError, TypeError):
        raise UpdateError(f"{url} is not a CLI manifest") from None


def replace_binary(
    staged: Path, target: Path, *, windows: bool, replace: Callable[[Path, Path], None]
) -> None:
    """Put `staged` in place of `target` in one rename; on Windows, rename the running one aside first."""
    if not windows:
        replace(staged, target)
        return
    aside = target.with_name(target.name + ".old")
    if aside.exists():
        with contextlib.suppress(OSError):  # still held by a process: it goes at the next update
            aside.unlink()
    moved_aside = False
    if target.exists():
        replace(target, aside)
        moved_aside = True
    try:
        replace(staged, target)
    except OSError:
        if moved_aside:
            replace(aside, target)
        raise
    if moved_aside:
        with contextlib.suppress(OSError):  # the running .exe is still mapped: removed at the next update
            aside.unlink()


@dataclass
class UpdateResult:
    previous: str
    version: str
    target: Path
    replaced: bool


def update(
    http: httpx.Client,
    api_url: str,
    target: Path,
    *,
    system: str,
    arch: str,
    current: str,
    force: bool = False,
    replace: Callable[[Path, Path], None] = os.replace,
) -> UpdateResult:
    """Download, verify and install the instance's binary over `target`. Nothing moves on any error."""
    manifest = fetch_manifest(http, api_url)
    if manifest.version == current and not force:
        return UpdateResult(previous=current, version=current, target=target, replaced=False)
    entry = manifest.entry(system, arch)
    name, expected = str(entry.get("name", "")), str(entry.get("sha256", "")).lower()
    if not name or "/" in name or "\\" in name or len(expected) != 64:
        raise UpdateError("the instance's manifest gives no usable name or sha256 for this platform")
    url = f"{api_url.rstrip('/')}/downloads/cli/{name}"
    staged = target.with_name(f".{target.name}.{os.getpid()}.new")
    digest = hashlib.sha256()
    try:
        try:
            with http.stream("GET", url) as response:
                if response.status_code != 200:
                    raise UpdateError(f"{url} answered {response.status_code}")
                with staged.open("wb") as out:
                    for chunk in response.iter_bytes():
                        digest.update(chunk)
                        out.write(chunk)
        except httpx.TransportError as exc:
            raise UpdateError(f"cannot download {url}: {exc or type(exc).__name__}") from None
        except PermissionError:
            raise UpdateError(f"cannot write next to {target}: pass --path to a binary you own") from None
        actual = digest.hexdigest()
        if actual != expected:
            raise UpdateError(
                f"checksum mismatch for {name}: expected {expected}, got {actual}; nothing was replaced"
            )
        if "size" in entry and staged.stat().st_size != int(entry["size"]):
            raise UpdateError(f"{name} does not have the size the manifest gives; nothing was replaced")
        if system != "windows":
            staged.chmod(0o755)
        replace_binary(staged, target, windows=system == "windows", replace=replace)
    finally:
        staged.unlink(missing_ok=True)
    return UpdateResult(previous=current, version=manifest.version, target=target, replaced=True)


# ─── the version check ───

_checked_this_process: set[str] = set()


def _state_path() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "choregos" / "version-check.json"


def _load_state(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (
        {str(k): float(v) for k, v in data.items() if isinstance(v, int | float)}
        if isinstance(data, dict)
        else {}
    )


def warning_line(current: str, served: str, api_url: str) -> str:
    if running_binary() is not None:
        how = "run `choregos self update`"
    else:
        how = f"upgrade the choregos-cli package, or install the instance's binary: {install_hint(api_url)}"
    return (
        f"choregos: this CLI is {current}, the instance at {api_url} runs {served}; {how} "
        f"({NO_CHECK_ENV}=1 silences this)"
    )


def check_version(
    api_url: str,
    get_edition: Callable[[], Any],
    *,
    current: str | None = None,
    now: float | None = None,
    write: Callable[[str], Any] | None = None,
) -> str | None:
    """Warn once on stderr when the instance runs another version; at most once a day per instance.

    `get_edition` returns `GET /api/v1/edition`'s body. Returns the line written, or None.
    """
    if os.environ.get(NO_CHECK_ENV, "").strip() not in {"", "0", "false"}:
        return None
    key = api_url.rstrip("/")
    if key in _checked_this_process:
        return None
    _checked_this_process.add(key)
    current = current or cli_version()
    if not known(current):
        return None
    moment = time.time() if now is None else now
    path = _state_path()
    state = _load_state(path)
    last = state.get(key)
    if last is not None and 0 <= moment - last < CHECK_EVERY_S:
        return None
    # Recorded before asking: an instance that answers slowly or not at all costs one try a day.
    state[key] = moment
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass
    try:
        served = str((get_edition() or {}).get("version") or "")
    except Exception:
        return None
    if not known(served) or served == current:
        return None
    line = warning_line(current, served, key)
    (write or (lambda text: print(text, file=sys.stderr)))(line)
    return line


# ─── `choregos self` ───

self_app = typer.Typer(help="The CLI itself: keep it in step with the instance")
console = Console()


@self_app.command("update")
def self_update(
    api_url: Annotated[
        str | None, typer.Option("--api-url", help="The instance to update from (default: the profile's)")
    ] = None,
    path: Annotated[
        Path | None,
        typer.Option("--path", help="The binary to replace (default: the running one)"),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Download even when the versions are the same")
    ] = False,
) -> None:
    """Replace this CLI with the instance's own build, for this OS and processor.

    The binary comes from the instance (`/downloads/cli/`), in the version it runs, and is checked
    against the sha256 of its manifest before it replaces the running one in a single rename. A CLI
    installed from Python packages has no binary to replace: upgrade the package, or install the
    binary with the instance's install script.
    """
    from .client import Profile

    url = (api_url or Profile.load().api_url).rstrip("/")
    target = path or running_binary()
    if target is None:
        console.print(
            "[red]✗[/red] this choregos runs from Python packages, not from a native binary: upgrade the "
            f"choregos-cli package, or install the instance's binary with: {install_hint(url)}",
            soft_wrap=True,
        )
        raise typer.Exit(1)
    try:
        system, arch = this_platform()
        with httpx.Client(timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=True) as http:
            result = update(http, url, target, system=system, arch=arch, current=cli_version(), force=force)
    except UpdateError as exc:
        console.print(f"[red]✗[/red] {exc}", soft_wrap=True)
        raise typer.Exit(1) from None
    if not result.replaced:
        console.print(f"[green]✓[/green] choregos {result.version} is already the version of {url}")
        return
    console.print(
        f"[green]✓[/green] choregos {result.previous} → {result.version} from {url}, at {result.target}",
        soft_wrap=True,
    )
