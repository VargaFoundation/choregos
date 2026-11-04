# SPDX-License-Identifier: Apache-2.0
"""Runner exit codes: defined in the core (`choregos_core.exit_codes`), which the executor also
reads to say why a pod died."""

from __future__ import annotations

from choregos_core.exit_codes import Exit

__all__ = ["Exit"]
