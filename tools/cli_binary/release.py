#!/usr/bin/env python3
"""Writes `SHA256SUMS` and `manifest.json` beside the native CLI binaries (S62-07).

    python tools/cli_binary/release.py dist/cli --version 0.25.3
    python tools/cli_binary/release.py dist/cli --version 0.25.3 --partial   # fewer than five

The file names and the manifest are a shared contract (S62-08 serves them from the instance, the
install scripts read the manifest):

    {"version": "x.y.z", "files": [{"name": "...", "os": "linux|macos|windows",
      "arch": "x86_64|arm64", "sha256": "...", "size": <bytes>}]}

`SHA256SUMS` is what `sha256sum -c` (`shasum -a 256 -c` on macOS) reads. A release refuses to write
them unless the five targets are all there: a release that silently lacked one would publish a
manifest whose missing platform nobody notices before a user does.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any

#: name -> (os, arch): the five targets, the same names as `build.py`'s.
TARGETS: dict[str, tuple[str, str]] = {
    "choregos-linux-x86_64": ("linux", "x86_64"),
    "choregos-linux-arm64": ("linux", "arm64"),
    "choregos-macos-arm64": ("macos", "arm64"),
    "choregos-macos-x86_64": ("macos", "x86_64"),
    "choregos-windows-x86_64.exe": ("windows", "x86_64"),
}
SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(directory: pathlib.Path, version: str, *, partial: bool = False) -> dict[str, Any]:
    if not SEMVER.match(version):
        raise ValueError(f"not a release version: {version!r}")
    present = [name for name in TARGETS if (directory / name).is_file()]
    missing = sorted(set(TARGETS) - set(present))
    if missing and not partial:
        raise ValueError(f"binaries missing from {directory}: {missing}")
    if not present:
        raise ValueError(f"no binary in {directory}")
    files = []
    for name in present:
        path = directory / name
        os_name, arch = TARGETS[name]
        files.append(
            {"name": name, "os": os_name, "arch": arch, "sha256": sha256(path), "size": path.stat().st_size}
        )
    return {"version": version, "files": files}


def sha256sums(doc: dict[str, Any]) -> str:
    return "".join(f"{entry['sha256']}  {entry['name']}\n" for entry in doc["files"])


def write(directory: pathlib.Path, version: str, *, partial: bool = False) -> dict[str, Any]:
    doc = manifest(directory, version, partial=partial)
    (directory / "manifest.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    (directory / "SHA256SUMS").write_text(sha256sums(doc), encoding="utf-8")
    return doc


def main() -> int:
    parser = argparse.ArgumentParser(description="SHA256SUMS and manifest.json for the CLI binaries.")
    parser.add_argument("directory", type=pathlib.Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--partial", action="store_true", help="accept fewer than the five targets")
    args = parser.parse_args()
    try:
        doc = write(args.directory, args.version, partial=args.partial)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for entry in doc["files"]:
        print(f"{entry['sha256']}  {entry['name']}  ({entry['size']} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
