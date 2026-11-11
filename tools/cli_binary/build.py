#!/usr/bin/env python3
"""Builds the `choregos` CLI as ONE native, self-contained file with Nuitka (S62-07).

    python tools/cli_binary/build.py                 # this machine's target, into dist/cli/
    python tools/cli_binary/build.py --out some/dir  # elsewhere
    python tools/cli_binary/build.py --manylinux     # Linux: in the manylinux_2_28 image (docker)
    make cli-binary                                  # the latter on Linux, the former elsewhere

Nuitka compiles the CLI and everything it imports to C, links it with the Python runtime, and packs
the result in one executable (`--mode=onefile`, standalone inside): the person who runs it installs
no Python, no SDK, no runtime. A build makes the binary of the machine it runs on — Nuitka does not
cross-compile — so the release builds each of the five targets on a native runner.

What goes in is exactly what the CLI needs, and nothing the workspace happens to have: a fresh
virtual environment receives the CLI's own dependencies, pinned by `uv.lock` (`uv export
--package choregos-cli`), then `choregos-contracts`, `choregos-core` and `choregos-cli` as wheels.
`choregos-core` comes WITHOUT its `telemetry` extra, so OpenTelemetry is not even installed, and
the build fails if Nuitka's report names one of its modules anyway.

The data the CLI reads at run time ships inside: the workflow templates, the policy presets and the
catalogues of `choregos_core`, the JSON Schemas and the OpenAPI of `choregos_contracts`
(`importlib.resources`), and the distribution metadata `choregos --version` reads.

On Linux the release builds with `--manylinux`: inside the PyPA `manylinux_2_28` image, whose
glibc is 2.28, so the binary runs on any distribution from RHEL 8, Debian 10 or Ubuntu 18.10 on; a
binary built on the runner itself would need that runner's glibc (2.39 on Ubuntu 24.04, which leaves
out RHEL 9 and Debian 12). The image brings its own Python 3.12; the script installs uv and ccache
in it and runs this same file.

Requires `uv` and a C compiler (gcc or clang; MSVC or the MinGW Nuitka downloads on Windows), or
docker with `--manylinux`. Nuitka's cache (and ccache's, inside it) lives in
`build/cli-binary/cache/` unless `NUITKA_CACHE_DIR` says otherwise: the CI keeps it between runs.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
ENTRY = HERE / "entry.py"
#: The stable, version-less file names (shared contract of S62-07 and S62-08): URLs never change.
TARGETS: dict[tuple[str, str], str] = {
    ("linux", "x86_64"): "choregos-linux-x86_64",
    ("linux", "arm64"): "choregos-linux-arm64",
    ("macos", "arm64"): "choregos-macos-arm64",
    ("macos", "x86_64"): "choregos-macos-x86_64",
    ("windows", "x86_64"): "choregos-windows-x86_64.exe",
}
#: The workspace packages the binary is built from, in install order.
PACKAGES = ("packages/contracts", "packages/core", "packages/cli")
#: Modules that must never be in the binary: the CLI does not trace, and the server is not its own.
FORBIDDEN_MODULES = ("opentelemetry", "choregos_orchestrator", "choregos_api", "sqlalchemy", "temporalio")
#: fnmatch patterns of the `pygments.lexers` modules left out: all but `python` and `_mapping`.
PYGMENTS_LEFT_OUT = ("[a-oq-z]*", "p[a-xz]*", "py[a-su-z]*", "_[a-ln-z]*", "_m[b-z]*")
#: The image of the Linux build (`--manylinux`), by architecture: glibc 2.28, a Python 3.12 inside.
MANYLINUX = {
    "x86_64": "quay.io/pypa/manylinux_2_28_x86_64:2026.10.26-1",
    "arm64": "quay.io/pypa/manylinux_2_28_aarch64:2026.10.26-1",
}
MANYLINUX_PYTHON = "/opt/python/cp312-cp312/bin/python3.12"
CACHE = ROOT / "build" / "cli-binary" / "cache"
_OS = {"linux": "linux", "darwin": "macos", "windows": "windows"}
_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}


def host_target() -> tuple[str, str]:
    """(os, arch) of this machine, in the manifest's words (`linux|macos|windows`, `x86_64|arm64`)."""
    system, machine = platform.system().lower(), platform.machine().lower()
    if system not in _OS or machine not in _ARCH:
        raise SystemExit(f"no native CLI binary for {platform.system()} {platform.machine()}")
    return _OS[system], _ARCH[machine]


def file_name(os_name: str, arch: str) -> str:
    try:
        return TARGETS[(os_name, arch)]
    except KeyError:
        raise SystemExit(f"no native CLI binary for {os_name} {arch}") from None


def cli_version() -> str:
    """The version of `packages/cli/pyproject.toml`, the one `tools/bump_version.py` sets."""
    text = (ROOT / "packages" / "cli" / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version = "([^"]+)"', text, re.M)
    if not found:
        raise SystemExit("packages/cli/pyproject.toml has no version")
    return found.group(1)


def numeric_version(version: str) -> str:
    """`0.25.3-rc.1` -> `0.25.3.0`: Windows' file version takes four numbers, nothing else."""
    numbers = [int(part) for part in re.findall(r"\d+", version.split("-", 1)[0])[:3]]
    return ".".join(str(n) for n in [*numbers, 0, 0, 0, 0][:4])


def source_digest() -> str:
    """A digest of what the binary is made of: the onefile cache directory is named after it, so a
    rebuilt binary never runs files a previous one of the same version unpacked."""
    digest = hashlib.sha256((ROOT / "uv.lock").read_bytes())
    digest.update(REQUIREMENTS.read_bytes())
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and "tests" not in path.parts:
                digest.update(str(path.relative_to(ROOT)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _run(command: list[str], **kwargs: object) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True, **kwargs)  # type: ignore[call-overload]  # noqa: S603


def _venv_python(venv: pathlib.Path) -> pathlib.Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepare_environment(work: pathlib.Path, python: str) -> pathlib.Path:
    """A virtual environment holding the CLI, its pinned dependencies and Nuitka; nothing else."""
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("`uv` is not on the PATH: https://docs.astral.sh/uv/")
    venv = work / "venv"
    if venv.exists():
        shutil.rmtree(venv)
    _run([uv, "venv", "--python", python, str(venv)])
    pinned = work / "requirements-cli.txt"
    _run(
        [
            uv,
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-workspace",
            "--package",
            "choregos-cli",
            "--no-hashes",
            "--no-header",
            "--no-annotate",
            "--output-file",
            str(pinned),
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
    )
    leaked = [line for line in pinned.read_text(encoding="utf-8").splitlines() if "opentelemetry" in line]
    if leaked:
        raise SystemExit(f"the CLI's dependencies carry OpenTelemetry again: {leaked}")
    python_path = str(_venv_python(venv))
    _run([uv, "pip", "install", "--python", python_path, "-r", str(pinned), "-r", str(REQUIREMENTS)])
    # Wheels, not editable installs: the binary must be built from what a release ships.
    _run([uv, "pip", "install", "--python", python_path, "--no-deps", *[str(ROOT / p) for p in PACKAGES]])
    return _venv_python(venv)


def nuitka_command(python: pathlib.Path, work: pathlib.Path, name: str, version: str) -> list[str]:
    numeric = numeric_version(version)
    command = [
        str(python),
        "-m",
        "nuitka",
        "--mode=onefile",
        f"--output-dir={work / 'nuitka'}",
        f"--output-filename={name}",
        f"--report={work / 'report.xml'}",
        "--assume-yes-for-downloads",
        # The data `choregos_core` and `choregos_contracts` read at run time.
        "--include-package-data=choregos_core",
        "--include-package-data=choregos_contracts",
        "--include-package-data=jsonschema_specifications",
        # `choregos --version` reads it (`importlib.metadata`).
        "--include-distribution-metadata=choregos-cli",
        # Typer finds the shell for `--install-completion` by importing a module by name.
        "--include-package=shellingham",
        # Rich loads its Unicode width tables by name too.
        "--include-package=rich._unicode_data",
        # Never followed even if present: the offline demo imports the orchestrator, lazily.
        "--nofollow-import-to=choregos_orchestrator",
        "--nofollow-import-to=opentelemetry",
        # Installed to compress the onefile payload, and an optional codec of httpx: not the CLI's.
        "--nofollow-import-to=zstandard",
        # Pygments' 260 lexers were a third of the compiled code; Rich highlights only Python
        # (a traceback). Every lexer module but `python` and the name table `_mapping` is left out,
        # by patterns: a `--nofollow-import-to` wins over an `--include-module`.
        *(f"--nofollow-import-to=pygments.lexers.{pattern}" for pattern in PYGMENTS_LEFT_OUT),
        # Unpacked once per build into the user's cache, then reused: a cold start pays the
        # extraction once, every later call starts from the unpacked files.
        f"--onefile-tempdir-spec={{CACHE_DIR}}/choregos/cli-{version}-{source_digest()}",
        "--company-name=Varga Foundation",
        "--product-name=choregos",
        "--file-description=Choregos command line",
        f"--product-version={numeric}",
        f"--file-version={numeric}",
        "--copyright=Apache-2.0",
        "--lto=no",
    ]
    identity = os.environ.get("CHOREGOS_MACOS_SIGN_IDENTITY")
    if sys.platform == "darwin" and identity:
        # Nuitka signs the onefile bootstrap and its payload itself; a `codesign` afterwards would
        # sign a file whose appended payload it does not know about.
        command += [f"--macos-sign-identity={identity}", "--macos-sign-notarization"]
    command.append(str(ENTRY))
    return command


def bundled_modules(report: pathlib.Path) -> list[str]:
    """The module names Nuitka's compilation report says it put in the binary."""
    tree = ET.parse(report)  # noqa: S314 - our own build's report
    return sorted({node.attrib["name"] for node in tree.iter("module") if "name" in node.attrib})


def forbidden_in(modules: list[str]) -> list[str]:
    return [m for m in modules if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_MODULES)]


def build(out: pathlib.Path, python: str) -> pathlib.Path:
    os_name, arch = host_target()
    name = file_name(os_name, arch)
    version = cli_version()
    work = ROOT / "build" / "cli-binary" / name
    work.mkdir(parents=True, exist_ok=True)
    interpreter = prepare_environment(work, python)
    # The environment's own tools first on the PATH: `patchelf` comes from its wheel.
    env = {**os.environ, "PATH": os.pathsep.join([str(interpreter.parent), os.environ.get("PATH", "")])}
    env.setdefault("NUITKA_CACHE_DIR", str(CACHE / "nuitka"))
    _run(nuitka_command(interpreter, work, name, version), cwd=work, env=env)
    modules = bundled_modules(work / "report.xml")
    leaked = forbidden_in(modules)
    if leaked:
        raise SystemExit(f"modules the CLI must not carry are in the binary: {leaked}")
    out.mkdir(parents=True, exist_ok=True)
    target = out / name
    shutil.copy2(work / "nuitka" / name, target)
    size = target.stat().st_size
    print(f"built {target} ({size / 1_000_000:.1f} MB, {len(modules)} modules, version {version})")
    return target


def container_command(out: pathlib.Path, image: str) -> list[str]:
    """`docker run` of `in_container.sh` in `image`, the repository and `out` mounted at their paths."""
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("--manylinux needs docker")
    out.mkdir(parents=True, exist_ok=True)
    ids = (os.getuid(), os.getgid()) if hasattr(os, "getuid") else (0, 0)
    mounts = [f"--volume={ROOT}:{ROOT}"]
    if not out.is_relative_to(ROOT):
        mounts.append(f"--volume={out}:{out}")
    return [
        docker,
        "run",
        "--rm",
        *mounts,
        f"--workdir={ROOT}",
        f"--env=HOST_UID={ids[0]}",
        f"--env=HOST_GID={ids[1]}",
        f"--env=OUT={out}",
        f"--env=PYTHON={MANYLINUX_PYTHON}",
        f"--env=NUITKA_CACHE_DIR={os.environ.get('NUITKA_CACHE_DIR', CACHE / 'nuitka')}",
        f"--env=UV_CACHE_DIR={CACHE / 'uv'}",
        image,
        "bash",
        str(HERE / "in_container.sh"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", type=pathlib.Path, default=ROOT / "dist" / "cli")
    parser.add_argument("--python", default="3.12", help="the interpreter uv builds with")
    parser.add_argument(
        "--manylinux", action="store_true", help="Linux: build in the manylinux_2_28 image (glibc 2.28)"
    )
    parser.add_argument("--container", help="Linux: build in this image instead (it must have PYTHON)")
    args = parser.parse_args()
    out = args.out.resolve()
    try:
        if args.manylinux or args.container:
            os_name, arch = host_target()
            if os_name != "linux":
                raise SystemExit("--manylinux and --container build Linux binaries, on Linux")
            _run(container_command(out, args.container or MANYLINUX[arch]))
            return 0
        build(out, args.python)
    except subprocess.CalledProcessError as exc:
        print(f"build failed: `{exc.cmd[0]}` exited with {exc.returncode}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
