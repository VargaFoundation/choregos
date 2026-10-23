# SPDX-License-Identifier: Apache-2.0
"""The console mirrors what each `web_search` backend declares (S40-01j).

The API does not serve a backend's declared data handling, so the connector form carries a copy
(`apps/web/src/lib/web-search-declared.json`) to show it and to refuse a widening. A copy that drifts
would let the form accept what the API refuses, or refuse what it accepts.
"""

from __future__ import annotations

import json
import pathlib

from choregos_adapters.web_search import BACKENDS, declared

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIRROR = ROOT / "apps" / "web" / "src" / "lib" / "web-search-declared.json"


def test_the_console_table_is_what_every_backend_declares() -> None:
    table = json.loads(MIRROR.read_text(encoding="utf-8"))
    assert set(table) == set(BACKENDS), "the console must list exactly the shipped backends"
    for name in BACKENDS:
        assert table[name] == declared(name).to_dict(), f"`{name}` declares something else than the console"
