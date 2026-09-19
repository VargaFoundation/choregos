"""Tests « live » : les adaptateurs face aux vrais services.

Ils ne tournent que si les identifiants sont dans l'environnement — jamais dans le dépôt
(AGENTS.md : aucun secret en dur). Sans eux, ils sont **ignorés**, pas verts : un test qui
ne s'exécute pas ne prouve rien, et le dire est la moitié du travail.

    CHOREGOS_LIVE_GITLAB_TOKEN=… CHOREGOS_LIVE_GITLAB_PROJECT=groupe/projet \\
    uv run pytest tests/live -m live
"""

from __future__ import annotations

import os

import pytest


def require(*names: str) -> dict[str, str]:
    """Rend les variables demandées, ou ignore le test en disant lesquelles manquent."""
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(f"test live ignoré — variables absentes : {', '.join(missing)}")
    return values
