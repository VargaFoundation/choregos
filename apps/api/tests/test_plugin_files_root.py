"""Where stored plugin files are materialised (#1777): per user by default, configurable."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tranibor_api.services import plugin_files


def test_default_root_belongs_to_the_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRANIBOR_PLUGIN_FILES_DIR")
    monkeypatch.setattr(plugin_files.tempfile, "gettempdir", lambda: str(tmp_path))
    assert plugin_files._cache_root() == tmp_path / f"choregos-plugin-files-{os.getuid()}"


def test_root_is_configurable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRANIBOR_PLUGIN_FILES_DIR", str(tmp_path / "elsewhere"))
    assert plugin_files._cache_root() == tmp_path / "elsewhere"
