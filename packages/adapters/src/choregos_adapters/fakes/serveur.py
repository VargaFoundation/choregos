# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:external
"""Former module path of `choregos_adapters.fakes.server` (ADR 0098 §3 (a), (f)).

`python -m choregos_adapters.fakes.serveur` still serves, for a chart that has not switched yet.
"""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .server import ApplicationDeDemo, application, main

__all__ = ["ApplicationDeDemo", "application", "main"]

if __name__ == "__main__":  # pragma: no cover - the former container command
    main()
else:
    replace_module(__name__, sys.modules["choregos_adapters.fakes.server"])
