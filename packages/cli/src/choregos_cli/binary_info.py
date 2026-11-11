# SPDX-License-Identifier: Apache-2.0
"""`choregos binary-info` (hidden): what a native binary knows about itself (S62-07, S62-08).

The smoke test of the native binaries (`tools/cli_binary/smoke.py`) reads it, inside the binary:
- the version `--version` prints, and the one the `choregos-cli` distribution metadata gives —
  `choregos self update` and the version check against the instance (S62-08) read the latter,
  and say nothing, or download again forever, when a build leaves the metadata out;
- the executable `self update` would replace. When S62-08's `self_update.running_binary` is in
  the CLI, that is what answers; otherwise the same rule, here: Nuitka defines `__compiled__` in
  every module it compiles, and in onefile mode `sys.argv[0]` is the binary as it was called, a
  bare name when a shell found it on the PATH.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from . import __version__


def _running_binary() -> Path | None:
    if "__compiled__" not in globals() and not getattr(sys, "frozen", False):
        return None
    called = sys.argv[0]
    if os.sep not in called and (os.altsep is None or os.altsep not in called):
        called = shutil.which(called) or called
    return Path(called).resolve()


def info() -> dict[str, Any]:
    try:
        from_metadata: str | None = metadata.version("choregos-cli")
    except metadata.PackageNotFoundError:
        from_metadata = None
    try:
        detector = importlib.import_module("choregos_cli.self_update")
        running, source = detector.running_binary(), "choregos_cli.self_update.running_binary"
    except ModuleNotFoundError:
        running, source = _running_binary(), "choregos_cli.binary_info"
    return {
        "version": __version__,
        "metadata_version": from_metadata,
        "compiled": "__compiled__" in globals(),
        "argv0": sys.argv[0],
        "running_binary": str(running) if running is not None else None,
        "detector": source,
    }


def binary_info() -> None:
    """Print, as JSON, what this CLI knows about itself (the binaries' smoke test reads it)."""
    sys.stdout.write(json.dumps(info(), indent=2) + "\n")
