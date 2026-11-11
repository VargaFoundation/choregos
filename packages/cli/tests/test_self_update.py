# SPDX-License-Identifier: Apache-2.0
"""S62-08: `choregos self update` and the version check against the instance."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from choregos_cli import self_update
from choregos_cli.self_update import UpdateError, check_version, replace_binary, update
from typer.testing import CliRunner

URL = "https://choregos.example.org"
NEW = b"#!/bin/sh\necho new\n"


def _manifest(content: bytes = NEW, *, version: str = "9.9.9", sha256: str | None = None) -> dict[str, Any]:
    files = [
        {
            "name": f"choregos-{os_name}-{arch}" + (".exe" if os_name == "windows" else ""),
            "os": os_name,
            "arch": arch,
            "sha256": sha256 or hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
        for os_name, arch in (
            ("linux", "x86_64"),
            ("linux", "arm64"),
            ("macos", "arm64"),
            ("macos", "x86_64"),
            ("windows", "x86_64"),
        )
    ]
    return {"version": version, "files": files}


def _serve(mock: respx.MockRouter, manifest: dict[str, Any], content: bytes = NEW) -> respx.Route:
    mock.get(f"{URL}/downloads/cli/manifest.json").mock(return_value=httpx.Response(200, json=manifest))
    return mock.get(url__regex=rf"{URL}/downloads/cli/choregos-.*").mock(
        return_value=httpx.Response(200, content=content)
    )


@pytest.fixture
def binary(tmp_path: Path) -> Path:
    target = tmp_path / "bin" / "choregos"
    target.parent.mkdir()
    target.write_bytes(b"#!/bin/sh\necho old\n")
    target.chmod(0o755)
    return target


def _update(target: Path, **kwargs: Any) -> self_update.UpdateResult:
    with httpx.Client() as http:
        options: dict[str, Any] = {"system": "linux", "arch": "x86_64", "current": "9.9.8", **kwargs}
        return update(http, URL, target, **options)


# ─── self update ───


def test_update_replaces_the_binary_with_the_verified_one(binary: Path) -> None:
    with respx.mock() as mock:
        download = _serve(mock, _manifest())
        result = _update(binary)
    assert download.calls.last.request.url == f"{URL}/downloads/cli/choregos-linux-x86_64"
    assert "authorization" not in download.calls.last.request.headers
    assert result.replaced and (result.previous, result.version) == ("9.9.8", "9.9.9")
    assert binary.read_bytes() == NEW
    assert stat.S_IMODE(binary.stat().st_mode) == 0o755
    assert sorted(p.name for p in binary.parent.iterdir()) == ["choregos"]


def test_a_checksum_mismatch_replaces_nothing(binary: Path) -> None:
    before = binary.read_bytes()
    with respx.mock() as mock:
        _serve(mock, _manifest(), content=NEW.replace(b"new", b"evil"))
        with pytest.raises(UpdateError, match=r"checksum mismatch.*nothing was replaced"):
            _update(binary)
    assert binary.read_bytes() == before
    assert sorted(p.name for p in binary.parent.iterdir()) == ["choregos"]


def test_the_same_version_downloads_nothing_unless_forced(binary: Path) -> None:
    with respx.mock() as mock:
        download = _serve(mock, _manifest())
        assert not _update(binary, current="9.9.9").replaced
        assert not download.called
        assert _update(binary, current="9.9.9", force=True).replaced
    assert binary.read_bytes() == NEW


def test_an_instance_without_a_cli_names_the_release(binary: Path) -> None:
    problem = {
        "code": "no_cli_bundled",
        "release_url": "https://github.com/VargaFoundation/choregos/releases/tag/v9.9.9",
    }
    with respx.mock() as mock:
        mock.get(f"{URL}/downloads/cli/manifest.json").mock(return_value=httpx.Response(404, json=problem))
        with pytest.raises(UpdateError, match=r"bundles no CLI; download it from .*/tag/v9\.9\.9"):
            _update(binary)


def test_a_platform_the_manifest_lacks_is_refused(binary: Path) -> None:
    manifest = _manifest()
    manifest["files"] = [f for f in manifest["files"] if f["os"] != "linux"]
    with respx.mock(assert_all_called=False) as mock:
        _serve(mock, manifest)
        with pytest.raises(UpdateError, match="no binary for linux/x86_64"):
            _update(binary)


def _windows_like_replace(locked: Path) -> Any:
    """`os.replace` as Windows does it for a running .exe: it can be renamed, never overwritten."""

    def replace(source: Path, destination: Path) -> None:
        if destination == locked and destination.exists():
            raise PermissionError(f"[WinError 5] access denied: {destination}")
        os.replace(source, destination)

    return replace


def test_on_windows_the_running_exe_is_renamed_aside_then_replaced(tmp_path: Path) -> None:
    target = tmp_path / "choregos.exe"
    target.write_bytes(b"old exe")
    (tmp_path / "choregos.exe.old").write_bytes(b"left by the previous update")
    with respx.mock() as mock:
        _serve(mock, _manifest())
        result = _update(target, system="windows", replace=_windows_like_replace(target))
    assert result.replaced
    assert target.read_bytes() == NEW
    assert sorted(p.name for p in tmp_path.iterdir()) == ["choregos.exe"]


def test_overwriting_a_running_exe_in_place_would_fail() -> None:
    """The guard of the test above: the POSIX path (one `replace` over the target) meets the lock."""
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        target, staged = Path(directory) / "choregos.exe", Path(directory) / "staged"
        target.write_bytes(b"old")
        staged.write_bytes(b"new")
        with pytest.raises(PermissionError):
            replace_binary(staged, target, windows=False, replace=_windows_like_replace(target))


def test_on_windows_a_failed_replace_puts_the_old_exe_back(tmp_path: Path) -> None:
    target, staged = tmp_path / "choregos.exe", tmp_path / "staged"
    target.write_bytes(b"old")
    staged.write_bytes(b"new")

    def refuse_the_new_one(source: Path, destination: Path) -> None:
        if source == staged:
            raise PermissionError("disk full")
        os.replace(source, destination)

    with pytest.raises(PermissionError):
        replace_binary(staged, target, windows=True, replace=refuse_the_new_one)
    assert target.read_bytes() == b"old"


def test_the_command_updates_the_given_binary(binary: Path) -> None:
    from choregos_cli.main import app

    with respx.mock() as mock:
        _serve(mock, _manifest())
        result = CliRunner().invoke(app, ["self", "update", "--api-url", URL, "--path", str(binary)])
    if sys.platform.startswith(("linux", "darwin")):
        assert result.exit_code == 0, result.output
        assert "→ 9.9.9" in result.output
        assert binary.read_bytes() == NEW


def test_the_command_from_python_packages_says_how_to_update() -> None:
    from choregos_cli.main import app

    result = CliRunner().invoke(app, ["self", "update", "--api-url", URL])
    assert result.exit_code == 1
    flat = " ".join(result.output.split())
    assert "runs from Python packages" in flat
    assert f"{URL}/cli/install." in flat


def test_a_nuitka_binary_called_by_its_bare_name_is_found_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "choregos").write_bytes(b"")
    (tmp_path / "choregos").chmod(0o755)
    monkeypatch.setitem(self_update.__dict__, "__compiled__", object())
    monkeypatch.setattr(sys, "argv", ["choregos", "whoami"])
    monkeypatch.setenv("PATH", str(tmp_path))
    assert self_update.running_binary() == (tmp_path / "choregos").resolve()


def test_a_python_install_has_no_binary_to_replace() -> None:
    assert self_update.running_binary() is None


# ─── the version check ───


@pytest.fixture
def check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    monkeypatch.delenv("CHOREGOS_NO_VERSION_CHECK", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    self_update._checked_this_process.clear()
    lines: list[str] = []
    yield lines
    self_update._checked_this_process.clear()


def _edition(version: str) -> Any:
    calls: list[int] = []

    def get() -> dict[str, str]:
        calls.append(1)
        return {"version": version}

    get.calls = calls  # type: ignore[attr-defined]
    return get


def test_another_version_warns_once_a_day(check: list[str]) -> None:
    edition = _edition("9.9.9")
    line = check_version(URL, edition, current="9.9.8", now=1000.0, write=check.append)
    assert check == [line]
    assert line is not None and "\n" not in line
    assert "this CLI is 9.9.8, the instance at https://choregos.example.org runs 9.9.9" in line
    assert "curl -fsSL https://choregos.example.org/cli/install.sh | sh" in line or "irm " in line
    assert "CHOREGOS_NO_VERSION_CHECK=1" in line
    # The same process: no second request.
    assert check_version(URL, edition, current="9.9.8", now=1001.0, write=check.append) is None
    # Another process, an hour later: the state file holds it back, without a request.
    self_update._checked_this_process.clear()
    assert check_version(URL, edition, current="9.9.8", now=1000.0 + 3600, write=check.append) is None
    assert len(edition.calls) == 1
    # The next day: once more.
    self_update._checked_this_process.clear()
    assert check_version(URL, edition, current="9.9.8", now=1000.0 + 86_400, write=check.append)
    assert len(check) == 2


def test_the_same_version_says_nothing(check: list[str]) -> None:
    assert check_version(URL, _edition("9.9.9"), current="9.9.9", now=1.0, write=check.append) is None
    assert check == []


def test_the_variable_silences_it(check: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_NO_VERSION_CHECK", "1")
    edition = _edition("9.9.9")
    assert check_version(URL, edition, current="9.9.8", now=1.0, write=check.append) is None
    assert edition.calls == []


def test_a_failing_check_fails_nothing(check: list[str]) -> None:
    def broken() -> Any:
        raise httpx.ConnectError("refused")

    assert check_version(URL, broken, current="9.9.8", now=1.0, write=check.append) is None
    assert check == []


def test_a_development_version_is_not_compared(check: list[str]) -> None:
    assert check_version(URL, _edition("0.0.0+unknown"), current="9.9.8", now=1.0, write=check.append) is None
    self_update._checked_this_process.clear()
    assert check_version(URL, _edition("9.9.9"), current="0.0.0+unknown", now=1.0, write=check.append) is None


def test_a_command_writes_the_warning_on_stderr_once(
    check: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_cli.main import app

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"api_url": URL, "token": "chg_x", "org": "acme"}))
    import choregos_cli.client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", config)
    monkeypatch.setattr(self_update, "cli_version", lambda: "9.9.8")
    me = {"email": "ada@example.org", "memberships": [{"org": "acme", "project_slug": None, "role": "admin"}]}
    with respx.mock(base_url=URL) as mock:
        mock.get("/api/v1/me").mock(return_value=httpx.Response(200, json=me))
        edition = mock.get("/api/v1/edition").mock(
            return_value=httpx.Response(200, json={"version": "9.9.9"})
        )
        result = CliRunner().invoke(app, ["whoami"])
        again = CliRunner().invoke(app, ["whoami"])
    assert result.exit_code == 0, result.output
    assert result.stderr.count("runs 9.9.9") == 1
    assert "runs 9.9.9" not in result.stdout
    assert "runs 9.9.9" not in again.stderr
    assert edition.call_count == 1
    state = json.loads((tmp_path / "cache" / "choregos" / "version-check.json").read_text())
    assert list(state) == [URL]
