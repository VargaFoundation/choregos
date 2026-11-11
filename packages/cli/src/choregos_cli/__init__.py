# SPDX-License-Identifier: Apache-2.0
"""CLI `choregos`."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .client import ApiError, Client, Profile

try:
    #: The release's version, read from the installed distribution: `tools/bump_version.py` sets it
    #: in `pyproject.toml`, and the native binary carries the distribution's metadata (S62-07).
    #: It said `0.1.0` whatever the release, in the `User-Agent` of every A2A call.
    __version__ = version("choregos-cli")
except PackageNotFoundError:  # a source tree on `sys.path`, never installed
    __version__ = "0+unknown"

__all__ = ["ApiError", "Client", "Profile", "__version__"]
