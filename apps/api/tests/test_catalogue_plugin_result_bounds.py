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


SHORT = {**TOOL, "name": "short_log", "result_max_bytes": 2048}
HUGE = {**TOOL, "name": "huge_log", "result_max_bytes": 1 << 30}
TOOLS = [TOOL, SHORT, HUGE]


async def lister(session, run, projet):
    return TOOLS


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


async def test_a_plugin_tool_declares_its_own_cap_bounded_by_the_ceiling(
    plugin: None, client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """S40-17b: a plugin's tool says how much of its answer the agent reads, as a catalogue entry does."""
    from choregos_api.config import get_settings

    run_id, headers = await _run(project["id"])
    listed = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)
    assert listed.status_code == 200, listed.text
    assert all("result_max_bytes" not in t for t in listed.json()["tools"]), "the agent is not told the cap"

    short = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/short_log", headers=headers, json={"lines": 200}
    )
    assert short.status_code == 200, short.text
    result = short.json()["result"]
    assert _size(result) <= 2048 < get_settings().broker_result_max_bytes, "its own cap, not the default"
    assert "[truncated by the platform:" in result["text"]

    monkeypatch.setattr(get_settings(), "broker_result_ceiling_bytes", 4096)
    huge = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/huge_log", headers=headers, json={"lines": 10_000}
    )
    assert huge.status_code == 200, huge.text
    assert _size(huge.json()["result"]) <= 4096, "the ceiling bounds a plugin's cap too"


@pytest.mark.parametrize("declared", [512, "65536", True, 2048.5])
async def test_a_plugin_tool_with_a_bad_cap_is_refused_not_defaulted(
    plugin: None, client: AsyncClient, project: dict[str, Any], declared: Any
) -> None:
    import plugin_verbose  # type: ignore[import-not-found]

    plugin_verbose.TOOLS.append({**plugin_verbose.TOOL, "name": "bad_log", "result_max_bytes": declared})
    run_id, headers = await _run(project["id"])
    listed = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)
    assert listed.status_code == 500, listed.text
    assert "bad_log" in listed.text and "plugin_verbose" in listed.text
    called = await client.post(f"/api/v1/internal/runs/{run_id}/tools/verbose_log", headers=headers, json={})
    assert called.status_code == 500, "a call does not slip past the declaration either"
