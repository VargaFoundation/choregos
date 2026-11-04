"""The fakes, families, memory, market data, identity, documents and storage adapters are English
(S45-09, ADR 0098), and `python -m choregos_adapters.fakes.serveur`, the former module path that the
chart runs until S45-33 switches it, still serves."""

from __future__ import annotations

import importlib
import importlib.util
import os
import pathlib
import socket
import subprocess
import sys
import time
from types import ModuleType

import httpx
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = "packages/adapters/src/choregos_adapters/"

#: What S45-09 owns. `fakes/` is scanned whole and its files owned by other stories are removed.
OWNED = (
    f"{SRC}fakes/",
    f"{SRC}families.py",
    f"{SRC}memory/",
    f"{SRC}marketdata/",
    f"{SRC}identity/",
    f"{SRC}documents/",
    f"{SRC}web_search/",
    f"{SRC}storage/",
    f"{SRC}oci/",
    f"{SRC}netops/",
    f"{SRC}notify/",
    f"{SRC}realtime_ws.py",
    f"{SRC}sealing.py",
)
NOT_OURS = (
    f"{SRC}fakes/mcp.py",  # S46-01
    f"{SRC}fakes/mcp_gateway.py",  # S46-04
    f"{SRC}fakes/plugin_tracker.py",  # the plugin stories
)
OWNED_TESTS = (
    "test_adf_and_lexical",
    "test_demo_cd",
    "test_confluence_source",
    "test_confluence_storage",
    "test_http_contracts",
    "test_cosign",
    "test_cosign_keyless_real",
    "test_cosign_real",
    "test_document_sources",
    "test_ecphoria",
    "test_entra",
    "test_fakes",
    "test_fake_hr",
    "test_served_fakes",
    "test_marketdata",
    "test_netops_reads",
    "test_netops_writes",
    "test_object_store",
    "test_oci_registry",
    "test_web_crawl_politeness",
    "test_web_search_backends",
    "test_fakes_in_english",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_09", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_owned_adapters_have_no_french() -> None:
    checker = _checker()
    tests = tuple(f"packages/adapters/tests/{name}.py" for name in OWNED_TESTS)
    try:
        found = checker.scan_tree((*OWNED, *tests))
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    findings = [str(f) for f in found if f.path not in NOT_OURS]
    assert findings == []


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_python_m_fakes_serveur_still_serves() -> None:  # lang: fr-ok (the former command's name)
    """The chart's container command is `python -m choregos_adapters.fakes.serveur`."""
    port = _free_port()
    env = {**os.environ, "CHOREGOS_DEMO_PORT": str(port), "CONNECTOR_DEMO_TOKEN": "t"}
    env.pop("CONNECTOR_DEMO_JETON", None)  # lang: fr-ok
    process = subprocess.Popen(
        [sys.executable, "-m", "choregos_adapters.fakes.serveur"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        answer: httpx.Response | None = None
        while time.monotonic() < deadline and answer is None:
            try:
                answer = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1.0)
            except httpx.HTTPError:
                assert process.poll() is None, "the server exited before answering"
                time.sleep(0.2)
        assert answer is not None and answer.json() == {"ok": True}
    finally:
        process.terminate()
        process.wait(timeout=10)
