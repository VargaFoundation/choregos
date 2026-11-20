"""Every workspace plugin reaches the images and the docker paths-filter (S80-19).

`plugins/*` members are in `[tool.uv.workspace]`: `uv sync --frozen` reads every member's metadata, so
a Dockerfile that runs it without copying one fails to build, and the API image must carry each
extension's code (ADR 0181). The docker job runs only when a path in its filter changes, so a plugin
absent from the filter ships unbuilt. Docker is not needed to guard this: the files are read as text.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _plugins() -> list[str]:
    members = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["workspace"]["members"]
    # A glob member (`plugins/*`) is expanded, so it cannot hide a plugin from the checks below.
    found = {
        path.relative_to(ROOT).as_posix()
        for m in members
        if m.startswith("plugins/")
        for path in ROOT.glob(m)
        if (path / "pyproject.toml").is_file()
    }
    return sorted(found)


def _syncing_dockerfiles() -> list[Path]:
    return sorted(p for p in (ROOT / "docker").glob("*.Dockerfile") if "uv sync" in p.read_text())


def test_there_are_plugins_and_dockerfiles_to_check() -> None:
    assert _plugins()
    assert {p.name for p in _syncing_dockerfiles()} >= {"api.Dockerfile", "runner.Dockerfile"}


def test_every_syncing_dockerfile_copies_every_plugin() -> None:
    missing = []
    for dockerfile in _syncing_dockerfiles():
        copies = [
            line.split()[1:]
            for line in dockerfile.read_text().splitlines()
            if line.startswith("COPY ") and not line.startswith("COPY --")
        ]
        sources = {c[0].rstrip("/") for c in copies if len(c) >= 2}
        for plugin in _plugins():
            if plugin not in sources and f"{plugin}/pyproject.toml" not in sources:
                missing.append(f"{dockerfile.name}: {plugin}")
    assert not missing, missing


def test_the_api_image_carries_each_plugin_code() -> None:
    text = (ROOT / "docker" / "api.Dockerfile").read_text()
    for plugin in _plugins():
        assert re.search(rf"^COPY {re.escape(plugin)} {re.escape(plugin)}$", text, re.M), plugin


def test_the_docker_paths_filter_lists_every_plugin() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    block = ci.split("            docker:\n", 1)[1].split("\n            # What `docker compose", 1)[0]
    for plugin in _plugins():
        assert f"'{plugin}/**'" in block, plugin
