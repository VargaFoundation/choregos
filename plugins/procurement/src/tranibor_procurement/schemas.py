# SPDX-License-Identifier: Apache-2.0
"""The JSON Schemas shipped with the plugin (ADR 0198): the shape the reading agent must produce."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

SCHEMAS_DIR: Final = Path(__file__).resolve().parent / "schemas"

OFFERS_STRUCTURED: Final = "offers_structured"


def schema_path(name: str) -> Path:
    return SCHEMAS_DIR / f"{name}.schema.json"


def load_schema(name: str) -> dict[str, Any]:
    """The schema `name` (without its `.schema.json` suffix), as a JSON document."""
    schema: dict[str, Any] = json.loads(schema_path(name).read_text(encoding="utf-8"))
    return schema


SUPPLIER_FILE: Final = "supplier_file"
