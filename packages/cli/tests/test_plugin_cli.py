# SPDX-License-Identifier: Apache-2.0
"""`choregos plugin validate / pack / index` (ADR 0093, S43-02): offline, exit 1 with every problem."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from choregos_cli.main import app
from typer.testing import CliRunner

HELLO = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "hello"
runner = CliRunner()


def test_validate_prints_the_digest() -> None:
    result = runner.invoke(app, ["plugin", "validate", str(HELLO)])
    assert result.exit_code == 0, result.output
    assert "hello 1.0.0 sha256:" in result.output


def test_validate_fails_on_an_invalid_plugin(tmp_path: Path) -> None:
    copy = tmp_path / "hello"
    shutil.copytree(HELLO, copy)
    server = copy / "servers" / "hello.yaml"
    server.write_text(server.read_text().replace("@sha256:", ":1.0.0#"))
    result = runner.invoke(app, ["plugin", "validate", str(copy)])
    assert result.exit_code == 1
    assert "servers/hello.yaml:image" in result.output


def test_pack_is_reproducible_and_validates_back(tmp_path: Path) -> None:
    first, second = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    assert runner.invoke(app, ["plugin", "pack", str(HELLO), "-o", str(first)]).exit_code == 0
    assert runner.invoke(app, ["plugin", "pack", str(HELLO), "--output", str(second)]).exit_code == 0
    assert first.read_bytes() == second.read_bytes()
    result = runner.invoke(app, ["plugin", "validate", str(first)])
    assert result.exit_code == 0, result.output


def test_index_writes_a_source_index(tmp_path: Path) -> None:
    out = tmp_path / "index.yaml"
    args = ["plugin", "index", str(HELLO.parent), "-o", str(out), "--oci-repository", "ghcr.io/acme/plugins"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(out.read_text())
    assert doc["kind"] == "PluginIndex"
    assert doc["plugins"]["hello"]["versions"]["1.0.0"]["oci"] == "ghcr.io/acme/plugins/hello:1.0.0"


def test_index_needs_a_location(tmp_path: Path) -> None:
    result = runner.invoke(app, ["plugin", "index", str(HELLO.parent), "-o", str(tmp_path / "i.yaml")])
    assert result.exit_code == 1
    assert not (tmp_path / "i.yaml").exists()
