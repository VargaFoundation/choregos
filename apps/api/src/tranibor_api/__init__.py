# SPDX-License-Identifier: Apache-2.0
"""Tranibor API: REST, webhooks, SSE, internal API, OIDC, RLS."""

from __future__ import annotations

from importlib import metadata as _metadata

import tranibor_contracts  # noqa: F401 - adopts the former environment variable names, first (ADR 0176 R4)

#: Read from the installed package's metadata, never copied: the constant said `0.1.0` while the
#: repository was at 0.4.1, and the API announced itself as `1.0.0` on top of that. Three versions,
#: two of them wrong. `tools/bump_version.py` now has nothing to touch here.
try:
    __version__ = _metadata.version("tranibor-api")
except _metadata.PackageNotFoundError:  # tree not installed (build, tooling)
    __version__ = "0.0.0+unknown"
