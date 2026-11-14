# SPDX-License-Identifier: Apache-2.0
"""S62-08: the instance serves its own CLI, and the install scripts install it from there.

The binaries are S62-07's; here a temporary `CHOREGOS_CLI_DIR` holds fake ones, with the manifest the
shared contract fixes. The install script is run for real, by `/bin/sh`, against a local server.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from choregos_core.publishing import PUBLISHING
from httpx import AsyncClient

NAMES = {
    ("linux", "x86_64"): "choregos-linux-x86_64",
    ("linux", "arm64"): "choregos-linux-arm64",
    ("macos", "arm64"): "choregos-macos-arm64",
    ("macos", "x86_64"): "choregos-macos-x86_64",
    ("windows", "x86_64"): "choregos-windows-x86_64.exe",
}


def fake_binary(name: str) -> bytes:
    """A 'binary' that the install test can run: it says which file it is."""
    return f'#!/bin/sh\necho "choregos 9.9.9 ({name})"\n'.encode()


def make_bundle(directory: Path, *, version: str = "9.9.9", tamper: str | None = None) -> dict[str, Any]:
    """The five files, `SHA256SUMS` and `manifest.json`; `tamper` names a file whose bytes change after."""
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    sums = []
    for (os_name, arch), name in NAMES.items():
        content = fake_binary(name)
        (directory / name).write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        files.append({"name": name, "os": os_name, "arch": arch, "sha256": digest, "size": len(content)})
        sums.append(f"{digest}  {name}\n")
    manifest = {"version": version, "files": files}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (directory / "SHA256SUMS").write_text("".join(sums), encoding="utf-8")
    (directory / "unlisted.txt").write_text("not in the manifest", encoding="utf-8")
    if tamper is not None:
        # The same size, other bytes: only the checksum can tell.
        original = (directory / tamper).read_bytes()
        (directory / tamper).write_bytes(original.replace(b"9.9.9", b"6.6.6"))
    return manifest


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    from choregos_api.config import reset_settings_cache

    directory = tmp_path / "cli"
    manifest = make_bundle(directory)
    monkeypatch.setenv("CHOREGOS_CLI_DIR", str(directory))
    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", "https://choregos.example.org/")
    reset_settings_cache()
    yield {"dir": directory, "manifest": manifest}
    reset_settings_cache()


# ─── the routes ───


async def test_the_manifest_is_served_as_written_without_any_sign_in(
    client: AsyncClient, bundle: dict[str, Any]
) -> None:
    response = await client.get("/downloads/cli/manifest.json")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    assert response.json() == bundle["manifest"]
    raw = (bundle["dir"] / "manifest.json").read_bytes()
    assert response.content == raw
    etag = response.headers["etag"]
    assert etag == f'"sha256-{hashlib.sha256(raw).hexdigest()}"'
    again = await client.get("/downloads/cli/manifest.json", headers={"If-None-Match": etag})
    assert again.status_code == 304


async def test_a_binary_carries_its_type_length_checksum_and_strong_etag(
    client: AsyncClient, bundle: dict[str, Any]
) -> None:
    entry = next(f for f in bundle["manifest"]["files"] if f["os"] == "windows")
    response = await client.get(f"/downloads/cli/{entry['name']}")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-length"] == str(entry["size"])
    assert response.headers["etag"] == f'"sha256-{entry["sha256"]}"'
    assert response.headers["x-checksum-sha256"] == entry["sha256"]
    assert entry["name"] in response.headers["content-disposition"]
    assert hashlib.sha256(response.content).hexdigest() == entry["sha256"]
    assert response.headers["accept-ranges"] == "bytes"
    cached = await client.get(
        f"/downloads/cli/{entry['name']}", headers={"If-None-Match": response.headers["etag"]}
    )
    assert cached.status_code == 304
    assert cached.content == b""


async def test_a_range_resumes_a_download(client: AsyncClient, bundle: dict[str, Any]) -> None:
    name = NAMES[("linux", "x86_64")]
    whole = (bundle["dir"] / name).read_bytes()
    response = await client.get(f"/downloads/cli/{name}", headers={"Range": "bytes=5-"})
    assert response.status_code == 206
    assert response.content == whole[5:]
    assert response.headers["content-range"] == f"bytes 5-{len(whole) - 1}/{len(whole)}"


async def test_the_checksums_file_is_served(client: AsyncClient, bundle: dict[str, Any]) -> None:
    response = await client.get("/downloads/cli/SHA256SUMS")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert NAMES[("macos", "arm64")] in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/downloads/cli/unlisted.txt",
        "/downloads/cli/..%2f..%2fetc%2fpasswd",
        "/downloads/cli/%2e%2e%2fmanifest.json",
        "/downloads/cli/choregos-linux-x86_64%2f..%2funlisted.txt",
        "/downloads/cli/choregos-freebsd-x86_64",
    ],
)
async def test_only_a_name_the_manifest_lists_is_served(
    client: AsyncClient, bundle: dict[str, Any], path: str
) -> None:
    response = await client.get(path)
    assert response.status_code == 404, response.text


async def test_without_a_bundle_the_answer_is_a_clear_404_naming_the_release(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import __version__
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_CLI_DIR", str(tmp_path / "absent"))
    reset_settings_cache()
    for path in ("/downloads/cli/manifest.json", "/downloads/cli/choregos-linux-x86_64"):
        response = await client.get(path)
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        problem = response.json()
        assert problem["code"] == "no_cli_bundled"
        assert problem["version"] == __version__
        assert problem["release_url"] == PUBLISHING.release(__version__)
        assert "bundles no `choregos` CLI" in problem["detail"]
    reset_settings_cache()


async def test_a_manifest_that_does_not_hold_is_refused_not_served(
    client: AsyncClient, bundle: dict[str, Any]
) -> None:
    manifest_path = bundle["dir"] / "manifest.json"
    broken = dict(bundle["manifest"])
    broken["files"] = [{**bundle["manifest"]["files"][0], "name": "../escape"}]
    manifest_path.write_text(json.dumps(broken), encoding="utf-8")
    response = await client.get("/downloads/cli/manifest.json")
    assert response.status_code == 503
    assert response.json()["code"] == "cli_bundle_unusable"
    manifest_path.write_text("{not json", encoding="utf-8")
    assert (await client.get("/downloads/cli/manifest.json")).status_code == 503


async def test_a_file_whose_size_is_not_the_manifests_is_not_served(
    client: AsyncClient, bundle: dict[str, Any]
) -> None:
    name = NAMES[("linux", "arm64")]
    (bundle["dir"] / name).write_bytes(b"truncated")
    response = await client.get(f"/downloads/cli/{name}")
    assert response.status_code == 503
    assert "size" in response.json()["detail"]
    (bundle["dir"] / name).unlink()
    assert (await client.get(f"/downloads/cli/{name}")).status_code == 503


async def test_the_install_scripts_carry_the_instances_url(
    client: AsyncClient, bundle: dict[str, Any]
) -> None:
    for name in ("install.sh", "install.ps1"):
        response = await client.get(f"/cli/install.{name.split('.')[1]}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert "__CHOREGOS_INSTANCE_URL__" not in response.text
        assert "https://choregos.example.org/downloads/cli/manifest.json" in response.text or (
            "'https://choregos.example.org'" in response.text
        )
    sh = (await client.get("/cli/install.sh")).text
    assert "INSTANCE_URL='https://choregos.example.org'" in sh
    ps1 = (await client.get("/cli/install.ps1")).text
    assert "[string]$InstanceUrl = 'https://choregos.example.org'" in ps1


async def test_a_public_url_that_is_not_plain_writes_no_script(
    client: AsyncClient, bundle: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", "https://x.example/'; rm -rf ~; '")
    reset_settings_cache()
    response = await client.get("/cli/install.sh")
    assert response.status_code == 503
    assert "rm -rf" not in response.text


@pytest.mark.parametrize(
    "url",
    [
        "https://choregos.example.org",
        "http://localhost:3000",
        "https://example.org/choregos",
        "http://[::1]:3000",
    ],
)
def test_the_rendering_accepts_plain_urls_only(url: str) -> None:
    from choregos_api.routers.cli_downloads import render_script

    if "[" in url:
        with pytest.raises(ValueError):
            render_script("install.sh", url)
        return
    assert f"INSTANCE_URL='{url}'" in render_script("install.sh", url)
    assert f"[string]$InstanceUrl = '{url}'" in render_script("install.ps1", url)
    for bad in (url + "'", url + "$(id)", url + " x", url + "`id`", url + '"', url + "\\x"):
        with pytest.raises(ValueError):
            render_script("install.sh", bad)


async def test_the_downloads_have_a_rate_limit_of_their_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.errors import install_error_handlers
    from choregos_api.rate_limit import install
    from choregos_api.routers.cli_downloads import router
    from fastapi import FastAPI
    from httpx import ASGITransport

    make_bundle(tmp_path / "cli")
    monkeypatch.setenv("CHOREGOS_CLI_DIR", str(tmp_path / "cli"))
    reset_settings_cache()
    app = FastAPI()
    install_error_handlers(app)
    install(app, per_minute=100, downloads_per_minute=2)
    app.include_router(router)

    @app.get("/api/v1/auth/probe")
    async def probe() -> dict[str, str]:
        return {"ok": "yes"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/downloads/cli/manifest.json")).status_code == 200
        assert (await http.get("/cli/install.sh")).status_code == 200
        refused = await http.get("/downloads/cli/choregos-linux-x86_64")
        assert refused.status_code == 429
        assert "2 requests per minute" in refused.json()["detail"]
        assert int(refused.headers["retry-after"]) >= 1
        # The sign-in budget is another one: the downloads did not spend it.
        assert (await http.get("/api/v1/auth/probe")).status_code == 200
    reset_settings_cache()


async def test_the_routes_are_served_at_the_root_outside_the_contract(app: Any, client: AsyncClient) -> None:
    import choregos_contracts as contracts

    # The other tests reach them through the whole application, at the root: none under `/api/v1`.
    assert (await client.get("/api/v1/downloads/cli/manifest.json")).status_code == 404
    contract = contracts.load_openapi()["paths"]
    assert not any(p.startswith(("/downloads", "/cli")) for p in contract)
    assert not any(p.startswith(("/downloads", "/cli")) for p in app.openapi()["paths"])


# ─── the install scripts, run ───


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """A real HTTP server on a local port, with only the download routes, over a fake bundle."""
    import uvicorn
    from choregos_api.config import reset_settings_cache
    from choregos_api.errors import install_error_handlers
    from choregos_api.routers.cli_downloads import router
    from fastapi import FastAPI

    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    directory = tmp_path / "cli"
    make_bundle(directory)
    monkeypatch.setenv("CHOREGOS_CLI_DIR", str(directory))
    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", url)
    reset_settings_cache()
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "the test server did not start"
        time.sleep(0.02)
    yield {"url": url, "dir": directory}
    server.should_exit = True
    thread.join(timeout=5)
    reset_settings_cache()


def _this_machine() -> tuple[str, str] | None:
    system = {"Linux": "linux", "Darwin": "macos"}.get(platform.system())
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine()
    )
    return (system, arch) if system and arch else None


needs_posix_and_curl = pytest.mark.skipif(
    _this_machine() is None or shutil.which("curl") is None or not Path("/bin/sh").exists(),
    reason="the install script runs on Linux and macOS, with curl",
)


def _install(url: str, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "SHELL": "/bin/zsh",
    }
    command = f"curl -fsSL {url}/cli/install.sh | /bin/sh -s -- " + " ".join(args)
    return subprocess.run(
        ["/bin/sh", "-c", command], env=env, capture_output=True, text=True, timeout=60, check=False
    )


@needs_posix_and_curl
def test_install_sh_installs_the_verified_binary_into_local_bin(
    live_instance: dict[str, Any], tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    done = _install(live_instance["url"], home)
    assert done.returncode == 0, done.stderr
    installed = home / ".local" / "bin" / "choregos"
    assert installed.is_file()
    assert os.access(installed, os.X_OK)
    name = NAMES[_this_machine()]  # type: ignore[index]
    assert installed.read_bytes() == (live_instance["dir"] / name).read_bytes()
    ran = subprocess.run([str(installed)], capture_output=True, text=True, check=True)
    assert ran.stdout.strip() == f"choregos 9.9.9 ({name})"
    assert "Installed choregos 9.9.9" in done.stdout
    # Not on PATH: the script says how to put it there, for the person's shell.
    assert f"echo 'export PATH=\"{home}/.local/bin:$PATH\"' >> ~/.zshrc" in done.stdout
    assert f"choregos login --api-url {live_instance['url']}" in done.stdout
    # Run again (an update): the same place, still one file, nothing staged left behind.
    again = _install(live_instance["url"], home)
    assert again.returncode == 0, again.stderr
    assert sorted(p.name for p in installed.parent.iterdir()) == ["choregos"]


@needs_posix_and_curl
def test_install_sh_honours_a_prefix(live_instance: dict[str, Any], tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "tools"
    done = _install(live_instance["url"], home, "--prefix", str(prefix))
    assert done.returncode == 0, done.stderr
    assert (prefix / "bin" / "choregos").is_file()
    assert not (home / ".local").exists()


@needs_posix_and_curl
def test_install_sh_refuses_a_binary_whose_checksum_differs(
    live_instance: dict[str, Any], tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    name = NAMES[_this_machine()]  # type: ignore[index]
    make_bundle(live_instance["dir"], tamper=name)
    done = _install(live_instance["url"], home)
    assert done.returncode != 0
    assert f"checksum mismatch for {name}" in done.stderr
    assert "nothing was installed" in done.stderr
    assert not (home / ".local" / "bin" / "choregos").exists()


@needs_posix_and_curl
def test_install_sh_says_when_the_instance_bundles_no_cli(
    live_instance: dict[str, Any], tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    shutil.rmtree(live_instance["dir"])
    done = _install(live_instance["url"], home)
    assert done.returncode != 0
    assert "this instance may bundle no CLI" in done.stderr
    assert PUBLISHING.releases in done.stderr


# ─── the scripts, read ───


def _script(name: str) -> str:
    from choregos_api.routers.cli_downloads import render_script

    return render_script(name, "https://choregos.example.org")  # type: ignore[arg-type]


def test_install_sh_is_valid_posix_sh(tmp_path: Path) -> None:
    script = tmp_path / "install.sh"
    script.write_text(_script("install.sh"), encoding="utf-8")
    for shell in ("/bin/sh", shutil.which("dash"), shutil.which("bash")):
        if shell and Path(shell).exists():
            checked = subprocess.run([shell, "-n", str(script)], capture_output=True, text=True, check=False)
            assert checked.returncode == 0, (shell, checked.stderr)
    shellcheck = shutil.which("shellcheck")
    if shellcheck:
        checked = subprocess.run(
            [shellcheck, "-s", "sh", str(script)], capture_output=True, text=True, check=False
        )
        assert checked.returncode == 0, checked.stdout
    text = _script("install.sh")
    assert "sudo " not in text.replace("never uses sudo", "").replace("never calls sudo", "")
    assert text.startswith("#!/bin/sh\n")


def test_install_ps1_stays_within_windows_powershell_5_1(tmp_path: Path) -> None:
    """No pwsh in CI: the static rules that keep it runnable by Windows PowerShell 5.1 and `iex`."""
    text = _script("install.ps1")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    # PowerShell 7 only: the null-coalescing and ternary operators, `&&`/`||` pipeline chains.
    for seven_only in ("??", " ? ", "&&", "||", "-AsHashtable"):
        assert seven_only not in code, seven_only
    # Run through `iex`, `exit` would close the person's window.
    assert not any(line.strip().startswith("exit") for line in code.splitlines())
    assert code.count("-UseBasicParsing") == 2
    assert "Get-FileHash -Algorithm SHA256" in code
    assert "Join-Path $env:LOCALAPPDATA 'Programs\\choregos'" in code
    assert "[Environment]::SetEnvironmentVariable('Path'" in code and "'User')" in code
    assert "choregos-windows-x86_64.exe" in code
    # The downloaded binary runs once before it replaces anything: a managed Windows may refuse to
    # start it (Defender ASR, found on a real machine on 2026-11-12), and nothing is installed then.
    assert code.index("& $downloaded --version") < code.index(
        "New-Item -ItemType Directory -Force -Path $InstallDir"
    )
    assert "Nothing was installed." in code
    # A running choregos.exe is renamed aside before the new one takes its name.
    assert code.index("-Destination $aside") < code.index(
        "Move-Item -LiteralPath $downloaded -Destination $target"
    )
    for opener, closer in (("{", "}"), ("(", ")"), ("[", "]")):
        assert code.count(opener) == code.count(closer), opener
    pwsh = shutil.which("pwsh")
    if pwsh:
        script = tmp_path / "install.ps1"
        script.write_text(text, encoding="utf-8")
        parse = (
            "$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile("
            f"'{script}', [ref]$null, [ref]$errors) | Out-Null; if ($errors) {{ $errors; exit 1 }}"
        )
        checked = subprocess.run(
            [pwsh, "-NoProfile", "-Command", parse], capture_output=True, text=True, check=False
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr
