# SPDX-License-Identifier: Apache-2.0
"""`tranibor-investment-research`: the extension of the `investment-research` plugin (ADR 0181).

A plugin is one unit with two halves under one name (`docs/site/build/plugins-and-trades.md`): its
content (the campaign template, its agents, skills, evals and connector procedures) is published from
`graalsystems/tranibor-agents`; this distribution is the code its trade runs inside the platform, loaded
from the `choregos.plugins` entry point (`extension.register`).
"""

from __future__ import annotations

__all__: list[str] = []
