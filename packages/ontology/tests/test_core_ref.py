# SPDX-License-Identifier: Apache-2.0
"""The reference package validates, compiles, and its IR and MCP tools match the golden snapshots.

Regenerate the snapshots on purpose only: ``UPDATE_SNAPSHOTS=1 uv run pytest packages/ontology``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from choregos_ontology import compile_directory
from choregos_ontology.validator import Registry

from .conftest import CORE_REF, SNAPSHOTS


def _golden(name: str, text: str) -> None:
    path = SNAPSHOTS / name
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.write_text(text, encoding="utf-8")
    assert path.exists(), f"missing snapshot {name}: run with UPDATE_SNAPSHOTS=1"
    assert text == path.read_text(encoding="utf-8"), f"{name} changed; review, then UPDATE_SNAPSHOTS=1"


def test_core_ref_is_valid() -> None:
    compiled, validation = compile_directory(CORE_REF)
    assert validation.valid, [issue.format() for issue in validation.errors]
    assert compiled is not None
    assert [o.name for o in compiled.object_types] == ["alert", "finding", "host", "service"]
    assert compiled.tests == ["escalation_refused_if_resolved", "open_finding_auto"]


def test_ir_matches_golden_snapshot() -> None:
    compiled, _ = compile_directory(CORE_REF)
    assert compiled is not None
    ir = json.loads(compiled.to_json())
    ir["checksum"] = "<checksum>"  # depends on file bytes, checked separately
    _golden("core-ref.ir.json", json.dumps(ir, indent=2, ensure_ascii=False) + "\n")


def test_mcp_tools_match_golden_snapshot() -> None:
    compiled, _ = compile_directory(CORE_REF)
    assert compiled is not None
    tools = [tool.model_dump() for tool in compiled.mcp_tools]
    _golden("core-ref.mcp.json", json.dumps(tools, indent=2, ensure_ascii=False) + "\n")


def test_compilation_is_deterministic(core_ref: Path) -> None:
    first, _ = compile_directory(CORE_REF)
    second, _ = compile_directory(core_ref)
    assert first is not None and second is not None
    assert first.to_json() == second.to_json()


def test_checksum_changes_with_the_sources(core_ref: Path) -> None:
    before, _ = compile_directory(core_ref)
    path = core_ref / "objects" / "host.yaml"
    path.write_text(path.read_text() + "\n# a comment changes the bytes\n")
    after, _ = compile_directory(core_ref)
    assert before is not None and after is not None
    assert before.checksum != after.checksum


def test_semantic_query_appears_only_with_embeddings(mutate, core_ref: Path) -> None:
    mutate(
        "objects/host.yaml",
        "  titleProperty: name\n",
        "  titleProperty: name\n  search:\n    semantic: {fields: [name]}\n",
    )
    without, validation = compile_directory(core_ref)
    assert without is not None
    assert [w.code for w in validation.warnings] == ["ONTW02"]
    search = next(t for t in without.mcp_tools if t.name == "host_search")
    assert "query" not in search.input_schema["properties"]
    with_model, validation = compile_directory(core_ref, Registry(embeddings=True))
    assert with_model is not None and not validation.warnings
    search = next(t for t in with_model.mcp_tools if t.name == "host_search")
    assert "query" in search.input_schema["properties"]


def test_unexposed_types_and_actions_have_no_tools(mutate, core_ref: Path) -> None:
    mutate("objects/service.yaml", "  primaryKey: id\n", "  primaryKey: id\n  mcp: {expose: false}\n")
    mutate("actions/escalate_finding.yaml", "  risk: high\n", "  risk: high\n  mcp: {expose: false}\n")
    compiled, _ = compile_directory(core_ref)
    assert compiled is not None
    names = {tool.name for tool in compiled.mcp_tools}
    assert not {"service_search", "service_get", "host_services", "service_host"} & names
    assert "action_escalate_finding" not in names
