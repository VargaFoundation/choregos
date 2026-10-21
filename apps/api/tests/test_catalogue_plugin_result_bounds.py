"""What a catalogue tool or a plugin returns, before the agent reads it (#759).

The broker's results were bounded and masked by #755; a catalogue provider's JSON and a plugin's
body still reached the agent as they came. Each test below fails without its guard.
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from .test_catalogue_outils import _intercepter, _run_avec_catalogue
from .test_greffons_outils import _run

pytestmark = pytest.mark.asyncio

PLUGIN = """
from choregos_api.greffons import declarer_un_fournisseur_d_outils

TOOL = {
    "name": "verbose_log",
    "description": "Reads a log.",
    "inputSchema": {"type": "object", "properties": {"lines": {"type": "integer"}}},
}


async def lister(session, run, projet):
    return [TOOL]


async def appeler(session, run, projet, nom, arguments):
    lines = arguments.get("lines", 1)
    return 200, {"log": ["line of a long log; password=hunter2-plugin"] * lines}


def brancher():
    declarer_un_fournisseur_d_outils("plugin_verbose", lister, appeler)
"""


@pytest.fixture
def plugin(tmp_path: pathlib.Path) -> Iterator[None]:
    from choregos_api.greffons import reinitialiser

    root = tmp_path / "site"
    root.mkdir()
    (root / "plugin_verbose.py").write_text(PLUGIN, encoding="utf-8")
    info = root / "plugin_verbose-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: plugin_verbose\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\nplugin_verbose = plugin_verbose:brancher\n", encoding="utf-8"
    )
    sys.path.insert(0, str(root))
    try:
        yield
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("plugin_verbose", None)
        reinitialiser()


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


async def _catalogue_call(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: dict[str, Any],
) -> httpx.Response:
    run_id, headers = await _run_avec_catalogue(project, tmp_path, monkeypatch)
    request = httpx.Request("GET", "https://api.annuaire.example/v1/search")
    _intercepter(monkeypatch, httpx.Response(200, json=answer, request=request), [])
    return await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils", headers=headers, json={"metier": "x"}
    )


async def test_an_oversized_catalogue_answer_is_cut_to_the_cap_with_a_marker(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import get_settings

    answer = await _catalogue_call(
        client, project, tmp_path, monkeypatch, {"profils": [{"nom": "a profile"}] * 20_000}
    )
    assert answer.status_code == 200, answer.text
    result = answer.json()["result"]
    limit = get_settings().broker_result_max_bytes
    assert _size(result) <= limit, "the cap holds"
    assert result["truncated"]["total_bytes"] > limit
    assert result["text"].startswith('{"profils"')
    assert "[truncated by the platform:" in result["text"]


async def test_a_secret_in_a_catalogue_answer_is_masked(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = await _catalogue_call(
        client,
        project,
        tmp_path,
        monkeypatch,
        {"profils": [{"nom": "A", "note": "password=hunter2-hunter2"}]},
    )
    assert answer.status_code == 200, answer.text
    assert "hunter2-hunter2" not in answer.text
    assert answer.json()["result"]["profils"][0]["nom"] == "A", "a small answer keeps its shape"


async def test_a_plugin_answer_is_masked_and_cut_to_the_cap(
    plugin: None, client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.config import get_settings

    run_id, headers = await _run(project["id"])
    url = f"/api/v1/internal/runs/{run_id}/tools/verbose_log"

    small = await client.post(url, headers=headers, json={"lines": 1})
    assert small.status_code == 200, small.text
    assert "hunter2-plugin" not in small.text
    assert small.json()["result"]["log"][0].startswith("line of a long log")

    big = await client.post(url, headers=headers, json={"lines": 10_000})
    assert big.status_code == 200, big.text
    result = big.json()["result"]
    assert _size(result) <= get_settings().broker_result_max_bytes
    assert "[truncated by the platform:" in result["text"]
    assert "hunter2-plugin" not in big.text
