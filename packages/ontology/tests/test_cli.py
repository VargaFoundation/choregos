# SPDX-License-Identifier: Apache-2.0
"""The minimal CLI: exit codes and outputs."""

from __future__ import annotations

import json

from choregos_ontology.cli import EXIT_INVALID, EXIT_OK, main

from .conftest import CORE_REF


def test_validate_ok(capsys):
    assert main(["validate", str(CORE_REF)]) == EXIT_OK
    assert capsys.readouterr().out.strip().endswith("valid")


def test_validate_invalid_exits_10_with_json(core_ref, mutate, capsys):
    mutate("objects/host.yaml", "  datasource: hosts", "  datasource: nowhere")
    assert main(["validate", str(core_ref), "--json"]) == EXIT_INVALID
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is False
    assert report["errors"][0]["code"] == "ONT005"
    assert report["errors"][0]["file"] == "objects/host.yaml"


def test_compile_writes_the_ir(tmp_path):
    out = tmp_path / "ir.json"
    assert main(["compile", str(CORE_REF), "--out", str(out)]) == EXIT_OK
    assert json.loads(out.read_text())["ir_version"] == "1"


def test_tools_lists_generated_names(capsys):
    assert main(["tools", str(CORE_REF)]) == EXIT_OK
    names = capsys.readouterr().out.split()
    assert "action_escalate_finding" in names and "host_services" in names
