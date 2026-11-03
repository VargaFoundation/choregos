# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:external — former module of choregos-admin, removed in 0.22.0 (ADR 0098)
"""The former name of `choregos_api.admin_cli`: `app` and `main` are the same objects."""

from __future__ import annotations

from .admin_cli import app, main

__all__ = ["app", "main"]

if __name__ == "__main__":
    main()
