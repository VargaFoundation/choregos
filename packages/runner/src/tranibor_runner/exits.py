# SPDX-License-Identifier: Apache-2.0
"""Runner exit codes: defined in the core (`tranibor_core.exit_codes`), which the executor also
reads to say why a pod died."""

from __future__ import annotations

from tranibor_core.exit_codes import Exit

__all__ = ["Exit"]
