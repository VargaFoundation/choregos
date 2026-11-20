# SPDX-License-Identifier: Apache-2.0
"""Canonical number and JSON formatting: the same data always yields the same bytes."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


def r6(x: float) -> float:
    """Round to 6 decimals; never `-0.0`, never NaN or infinity (replaced by 0.0)."""
    if math.isnan(x) or math.isinf(x):
        return 0.0
    return round(x, 6) + 0.0


def num(x: float) -> str:
    """Canonical text of a float for CSV files."""
    return format(x, ".10g")


def dumps(obj: Any) -> str:
    """Canonical JSON: sorted keys, fixed separators, ASCII, trailing newline."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"


def write_text(path: Path, text: str) -> None:
    """Write with `\\n` line endings whatever the platform."""
    path.write_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
