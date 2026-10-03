# SPDX-License-Identifier: Apache-2.0
"""One red test per implemented validation code; the green test is the valid reference package."""

from __future__ import annotations

from pathlib import Path

import pytest
from choregos_ontology import compile_directory
from choregos_ontology.compiler import compile_package
from choregos_ontology.errors import OntologyError
from choregos_ontology.loader import load_package
from choregos_ontology.validator import NOT_YET, Registry


def _codes(root: Path, registry: Registry | None = None) -> dict[str, list[str]]:
    _, validation = compile_directory(root, registry)
    found: dict[str, list[str]] = {}
    for issue in validation.errors:
        found.setdefault(issue.code, []).append(f"{issue.file}:{issue.line}")
    return found


def _add(root: Path, file: str, text: str) -> None:
    path = root / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


HEADER = "apiVersion: choregos.dev/v1alpha1\n"


def test_ont001_schema_error_is_located(core_ref, mutate):
    mutate("objects/host.yaml", "  primaryKey: id\n", "  primaryKey: id\n  colour: blue\n")
    issues = _codes(core_ref)
    assert issues["ONT001"] == ["objects/host.yaml:7"]


def test_ont001_invalid_yaml(core_ref):
    _add(core_ref, "objects/broken.yaml", HEADER + "kind: ObjectType\nmetadata: {name: [\n")
    assert "ONT001" in _codes(core_ref)


def test_ont001_exactly_one_ontology(core_ref):
    (core_ref / "ontology.yaml").unlink()
    assert "ONT001" in _codes(core_ref)


def test_ont002_unknown_api_version_and_kind(core_ref, mutate):
    mutate("objects/alert.yaml", "choregos.dev/v1alpha1", "choregos.dev/v9")
    _add(core_ref, "objects/odd.yaml", HEADER + "kind: Gadget\nmetadata: {name: odd}\nspec: {}\n")
    assert sorted(_codes(core_ref)["ONT002"]) == ["objects/alert.yaml:1", "objects/odd.yaml:2"]


def test_ont003_invalid_names(core_ref, mutate):
    mutate("objects/alert.yaml", "metadata: {name: alert,", "metadata: {name: Alert-1,")
    mutate("objects/host.yaml", "    os_version: {type: string}", "    OsVersion: {type: string}")
    mutate("actions/open_finding.yaml", "    severity: {type: string", "    Severity: {type: string")
    assert len(_codes(core_ref)["ONT003"]) >= 3


def test_ont004_duplicate_name(core_ref):
    _add(core_ref, "objects/host2.yaml", (core_ref / "objects" / "host.yaml").read_text())
    assert _codes(core_ref)["ONT004"] == ["objects/host2.yaml:3"]


def test_ont005_unknown_datasource(core_ref, mutate):
    mutate("objects/host.yaml", "  datasource: hosts", "  datasource: nowhere")
    assert _codes(core_ref)["ONT005"] == ["objects/host.yaml:5"]


def test_ont006_unknown_object_type(core_ref, mutate):
    mutate("links/host_runs_service.yaml", "to: {objectType: service,", "to: {objectType: daemon,")
    assert "ONT006" in _codes(core_ref)


def test_ont007_unknown_policy(core_ref, mutate):
    mutate("actions/open_finding.yaml", "approve: policy:core_ref", "approve: policy:missing")
    assert "ONT007" in _codes(core_ref)


def test_ont008_unknown_window(core_ref, mutate):
    mutate("actions/escalate_finding.yaml", "  risk: high\n", "  risk: high\n  window: night\n")
    assert "ONT008" in _codes(core_ref)


def test_ont010_unknown_property_type(core_ref, mutate):
    mutate("objects/host.yaml", "os_version: {type: string}", "os_version: {type: varchar}")
    assert "ONT010" in _codes(core_ref)


def test_ont011_invalid_primary_key(core_ref, mutate):
    mutate("objects/host.yaml", "  primaryKey: id", "  primaryKey: os")
    assert "ONT011" in _codes(core_ref)


def test_ont012_mapping(core_ref, mutate):
    mutate(
        "datasources/tables.yaml",
        "metadata: {name: hosts}\nspec: {type: table}",
        "metadata: {name: hosts}\nspec: {type: table, mapping: {id: hostname}}",
    )
    mutate("datasources/tables.yaml", "summary: title,", "summary: title, colour: tint,")
    assert len(_codes(core_ref)["ONT012"]) == 2


def test_ont013_cel_syntax(core_ref, mutate):
    mutate("actions/escalate_finding.yaml", "expr: \"target.status == 'open'\"", 'expr: "target.status == "')
    mutate("policies/core_ref.yaml", "when: \"action.risk == 'low'\"", "when: \"action.risk ==== 'low'\"")
    assert len(_codes(core_ref)["ONT013"]) == 2


def test_ont013_cel_in_effect_values(core_ref, mutate):
    mutate("actions/open_finding.yaml", 'key: "${params.key}", source', 'key: "${params.}", source')
    assert "ONT013" in _codes(core_ref)


def test_ont016_derived_collision(core_ref, mutate):
    mutate(
        "objects/host.yaml",
        "  titleProperty: name\n",
        '  titleProperty: name\n  derived:\n    os: {type: string, expr: "obj.os"}\n',
    )
    assert "ONT016" in _codes(core_ref)


def test_ont018_compensable_without_compensation(core_ref, mutate):
    mutate("actions/open_finding.yaml", "reversibility: reversible", "reversibility: compensable")
    assert "ONT018" in _codes(core_ref)


def test_ont019_irreversible_reachable_by_auto(core_ref, mutate):
    mutate("policies/core_ref.yaml", "when: \"action.risk == 'low'\"", 'when: "true"')
    assert _codes(core_ref)["ONT019"] == ["actions/escalate_finding.yaml:12"]


def test_ont019_undecidable_rule_counts_as_reachable(core_ref, mutate):
    mutate("policies/core_ref.yaml", "when: \"action.risk == 'low'\"", 'when: "params.size < 3"')
    assert "ONT019" in _codes(core_ref)


def test_ont020_missing_evidence(core_ref, mutate):
    mutate("actions/escalate_finding.yaml", "  evidence:\n", "  ignored:\n")
    # the key change makes ONT001 (unknown key); remove the block instead
    text = (core_ref / "actions/escalate_finding.yaml").read_text()
    head = text.split("  ignored:\n")[0]
    (core_ref / "actions/escalate_finding.yaml").write_text(head)
    assert "ONT020" in _codes(core_ref)


def test_ont021_missing_step_up(core_ref, mutate):
    mutate("policies/core_ref.yaml", "      stepUp: {maxAgeMinutes: 10}\n", "")
    assert _codes(core_ref)["ONT021"] == ["actions/escalate_finding.yaml:10"]


def test_ont022_sequencing_without_many(core_ref, mutate):
    mutate(
        "actions/escalate_finding.yaml",
        "  risk: high\n",
        "  risk: high\n  sequencing: {mode: one_at_a_time}\n",
    )
    assert "ONT022" in _codes(core_ref)


def test_ont023_semantic_field_not_string(core_ref, mutate):
    mutate(
        "objects/host.yaml",
        "  titleProperty: name\n",
        "  titleProperty: name\n  search:\n    semantic: {fields: [last_seen_at]}\n",
    )
    assert "ONT023" in _codes(core_ref)


def test_ont025_reserved_link_name_and_duplicate_tool(core_ref, mutate):
    mutate("links/host_runs_service.yaml", "name: services,", "name: search,")
    issues = _codes(core_ref)
    assert "ONT025" in issues


def test_ont025_tool_name_too_long(core_ref, mutate):
    long_name = "a" + "b" * 60
    mutate("actions/open_finding.yaml", "metadata: {name: open_finding,", f"metadata: {{name: {long_name},")
    mutate("tests/open_finding_auto.yaml", "action: open_finding", f"action: {long_name}")
    assert "ONT025" in _codes(core_ref)


def test_ont026_iceberg_needs_sql_engine(core_ref, mutate):
    mutate(
        "datasources/tables.yaml",
        "metadata: {name: hosts}\nspec: {type: table}",
        "metadata: {name: hosts}\nspec: {type: iceberg, table: it.hosts, mapping: {id: hostname}}",
    )
    assert "ONT026" in _codes(core_ref)
    assert "ONT026" not in _codes(core_ref, Registry(sql_engine=True))


def test_ont027_unknown_effect_and_evidence(core_ref, mutate):
    mutate("actions/escalate_finding.yaml", "- type: notify", "- type: page_someone")
    mutate("actions/escalate_finding.yaml", "collect: {type: object.reread}", "collect: {type: crystal_ball}")
    assert len(_codes(core_ref)["ONT027"]) == 2
    plugin = Registry(
        effects=frozenset({"object.create", "object.update", "page_someone"}),
        evidence=frozenset({"crystal_ball"}),
    )
    assert "ONT027" not in _codes(core_ref, plugin)


def test_all_errors_are_reported_not_only_the_first(core_ref, mutate):
    mutate("objects/host.yaml", "  datasource: hosts", "  datasource: nowhere")
    mutate("actions/open_finding.yaml", "approve: policy:core_ref", "approve: policy:missing")
    assert {"ONT005", "ONT007"} <= set(_codes(core_ref))


def test_compile_refuses_an_invalid_package(core_ref, mutate):
    mutate("objects/host.yaml", "  datasource: hosts", "  datasource: nowhere")
    package, _ = load_package(core_ref)
    with pytest.raises(OntologyError) as raised:
        compile_package(package)
    assert raised.value.validation.errors[0].code == "ONT005"


def test_not_yet_lists_only_unimplemented_codes():
    assert set(NOT_YET).isdisjoint({"ONT001", "ONT013", "ONT019", "ONT025", "ONT027"})
