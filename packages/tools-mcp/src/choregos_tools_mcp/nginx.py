# SPDX-License-Identifier: Apache-2.0
"""`validate_nginx_config`: `nginx -t` on a configuration of the run's workspace (ADR 0052).

It runs where the runner runs — the runner image carries nginx — on the files the agent has in its
workspace, which is the GitOps repository checked out: the configuration as it WOULD be merged.
Nothing is started and nothing listens: `-t` parses, loads what the configuration names and stops.

What it cannot know: files the configuration names by absolute path that live only on the server
(certificates, `mime.types` of another distribution). nginx reports them as it would on the server,
and the agent reads that report — it is not hidden as a success.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path

TIMEOUT_S = 30.0
MAX_REPORT = 4000


class OutsideWorkspaceError(ValueError):
    """A path that resolves outside the workspace (`..`, an absolute path, a symbolic link)."""


def config_path(workspace: Path, path: str) -> tuple[Path, Path]:
    root = workspace.resolve()
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise OutsideWorkspaceError(f"{path} is outside the workspace")
    if not target.is_file():
        raise FileNotFoundError(f"{path} is not a file of the workspace")
    return root, target


async def nginx_test(binary: str, workspace: Path, path: str) -> tuple[bool, str]:
    """(valid, nginx's report). Relative `include`s resolve from the main file's directory, as on
    the server; relative log paths go to a throwaway prefix, never into the workspace."""
    root, target = config_path(workspace, path)
    with tempfile.TemporaryDirectory(prefix="choregos-nginx-") as prefix:
        (Path(prefix) / "logs").mkdir()
        try:
            process = await asyncio.create_subprocess_exec(
                binary, "-t", "-e", "stderr", "-p", f"{prefix}/", "-c", str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )  # fmt: skip
        except FileNotFoundError:
            return False, (
                "nginx is not installed in this container: the runner image carries it, a sidecar does not. "
                "The configuration was NOT validated."
            )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=TIMEOUT_S)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            return (
                False,
                f"nginx -t did not finish in {TIMEOUT_S:.0f} s. The configuration was NOT validated.",
            )
    report = (stderr or stdout).decode(errors="replace").replace(f"{root}/", "")
    return process.returncode == 0, report.strip()[:MAX_REPORT]
