#!/usr/bin/env python3
"""Prints the Dockerfile the platform writes for a zip example of `examples/hosted/` (ADR 0080 §9).

    uv run python tools/hosted_dockerfile.py examples/hosted/claude-agent-sdk-node
    docker build -f <(uv run python tools/hosted_dockerfile.py DIR) DIR

The example's `choregos.json` holds what a version would say of its zip (`runtime`, `entry_point`,
`dependency_resolution`, optional `port`). The directory is zipped as an upload would be (without
`node_modules/`, `bin/`, `obj/` or hidden files), checked as the API checks it, and the Dockerfile
comes from the same function as the platform's: a zip example that the platform would refuse fails
here, and the nightly builds exactly what a publication would.
"""

from __future__ import annotations

import io
import json
import pathlib
import sys
import zipfile
from typing import Any

from choregos_api.schemas import HostedRuntime
from choregos_api.services.hosted_sources import SourceArchive, check_against, dockerfile, read_source

#: What an upload of the directory leaves out: what an install or a build writes there.
SKIPPED = frozenset({"node_modules", "bin", "obj", "__pycache__"})


def zipped(directory: pathlib.Path) -> bytes:
    """The directory's files at the root of a zip, as `zip -r ../agent.zip .` run inside it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(directory.rglob("*")):
            parts = path.relative_to(directory).parts
            if path.is_file() and not any(part in SKIPPED or part.startswith(".") for part in parts):
                archive.write(path, "/".join(parts))
    return buffer.getvalue()


def version(directory: pathlib.Path, archive: SourceArchive) -> HostedRuntime:
    """The hosted runtime a version of this example declares, checked against its zip."""
    declared: dict[str, Any] = json.loads((directory / "choregos.json").read_text())
    runtime = HostedRuntime.model_validate(
        {
            "source": {
                "type": "zip",
                "sha256": archive.sha256,
                "runtime": declared["runtime"],
                "entry_point": declared["entry_point"],
                "dependency_resolution": declared.get("dependency_resolution", "remote_build"),
            },
            "port": declared.get("port", 8088),
        }
    )
    assert runtime.source.type == "zip"
    check_against(archive, runtime.source)
    return runtime


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    directory = pathlib.Path(argv[0])
    archive = read_source(zipped(directory))
    sys.stdout.write(dockerfile(version(directory, archive), archive))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
