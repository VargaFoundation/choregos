# SPDX-License-Identifier: Apache-2.0
"""CLI `choregos`."""

from __future__ import annotations

from importlib import metadata as _metadata

import tranibor_contracts  # noqa: F401 - adopts the former environment variable names, first (ADR 0176 R4)

from .client import ApiError, Client, Profile

#: Read from the installed package's metadata, as the API does: the constant said `0.1.0` while the
#: package was at 0.25.3, and the version check against the instance (S62-08) compares this one.
#: A native binary (S62-07) carries the metadata of the `tranibor-cli` distribution.
try:
    __version__ = _metadata.version("tranibor-cli")
except _metadata.PackageNotFoundError:  # tree not installed
    __version__ = "0.0.0+unknown"

__all__ = ["ApiError", "Client", "Profile"]
