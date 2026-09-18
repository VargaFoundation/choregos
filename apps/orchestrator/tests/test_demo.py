"""La démonstration hors ligne : le scénario M1 doit passer en entier, sans cluster."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


async def test_demo_runs_end_to_end(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/demo.db")
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_orchestrator.activities.base import set_adapters_override
    from choregos_orchestrator.demo import main_async

    reset_settings_cache()
    await db_session.dispose_engine()
    try:
        code = await main_async()
    finally:
        set_adapters_override(None)
        await db_session.dispose_engine()
        reset_settings_cache()

    assert code == 0
    output = capsys.readouterr().out
    assert "état final du ticket : deployed_prod" in output
    assert "Choregos — suivi" in output, "le commentaire de suivi est écrit dans le ticket"
    assert "agent refine" in output and "agent implement" in output and "agent verify" in output
    assert "Validation" in output, "l'étape humaine apparaît dans le tableau"
    assert "findings déposés     : 1" in output
    assert "PR ouverte           : https://" in output
    assert os.environ["CHOREGOS_FAKES"] == "1"
