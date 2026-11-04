# SPDX-License-Identifier: Apache-2.0
"""The structured fields Choregos writes in a tracker, and their former names (S22-21).

Until S22-21, the orchestrator wrote `Coût (€)`, `Taille` and `Risque`, and the templates created
the GitHub Projects board under those names. Everything a user reads is in English (ADR 0039): the
fields are now called `Cost (€)`, `Size` and `Risk`. A board, a Jira project or a GitLab metadata
block created before keeps its French names: provisioning renames the board's field, and until
then each adapter writes in whichever of the two exists rather than lose the value.

The only French literals of this module are those former names: names that a tracker still
carries, not text that Choregos produces.
"""

from __future__ import annotations

from collections.abc import Container

COST = "Cost (€)"
SIZE = "Size"
RISK = "Risk"
RUN = "Run"

#: Today's name -> the name before S22-21. To remove one release after S22-21.
FORMER_NAMES: dict[str, str] = {
    COST: "Coût (€)",  # lang: fr-ok compatibility data
    SIZE: "Taille",  # lang: fr-ok compatibility data
    RISK: "Risque",  # lang: fr-ok compatibility data
}

#: The name before -> today's name.
CURRENT_NAMES: dict[str, str] = {former: current for current, former in FORMER_NAMES.items()}


def current_name(name: str) -> str:
    """Today's name of a field, whether it is given under the former or the new one."""
    return CURRENT_NAMES.get(name, name)


def present_name(name: str, present: Container[str]) -> str | None:
    """The name the field exists under: the new one first, the former otherwise, `None` if neither."""
    current = current_name(name)
    if current in present:
        return current
    former = FORMER_NAMES.get(current)
    if former is not None and former in present:
        return former
    return None
