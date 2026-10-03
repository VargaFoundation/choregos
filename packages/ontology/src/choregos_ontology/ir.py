# SPDX-License-Identifier: Apache-2.0
"""The intermediate representation (IR) of a compiled ontology (requirement R-SOC-ONT-02).

The IR is what every consumer reads: the action engine, the MCP server, the SQL compiler. It is
engine-independent, versioned (``ir_version``) and deterministic: compiling the same package twice
gives byte-identical JSON, which is what the golden tests compare.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

IR_VERSION = "1"


class _Ir(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IrProperty(_Ir):
    name: str
    type: str
    required: bool
    enum: list[str] | None = None
    format: str | None = None
    sensitivity: str
    searchable: bool
    filterable: bool
    sortable: bool


class IrDerived(_Ir):
    name: str
    type: str
    expr: str


class IrDatasource(_Ir):
    name: str
    type: str
    table: str | None = None
    sql: str | None = None
    connector: str | None = None
    source: str | None = None
    cache: str | None = None
    mapping: dict[str, str] | None = None


class IrSemantic(_Ir):
    fields: list[str]
    model: str


class IrObjectType(_Ir):
    name: str
    display_name: str | None
    description: str | None
    datasource: IrDatasource
    primary_key: str
    title_property: str
    properties: list[IrProperty]
    derived: list[IrDerived]
    semantic: IrSemantic | None
    freshness_max_age: str | None
    mcp_exposed: bool


class IrLinkEnd(_Ir):
    object_type: str
    name: str
    cardinality: str


class IrLinkType(_Ir):
    name: str
    from_: IrLinkEnd = Field(serialization_alias="from")
    to: IrLinkEnd
    join: dict[str, str]


class IrStep(_Ir):
    type: str
    with_: dict[str, Any] = Field(serialization_alias="with")


class IrEvidence(_Ir):
    name: str
    collect: IrStep
    expect: str


class IrActionType(_Ir):
    name: str
    display_name: str | None
    description: str | None
    target_type: str | None
    cardinality: str
    parameters: list[IrProperty]
    parameters_schema: dict[str, Any]
    preconditions: list[dict[str, str | None]]
    facts_max_age: str
    propose: list[str]
    approve_policy: str
    risk: str
    reversibility: str
    window: str | None
    effects: list[IrStep]
    sequencing: dict[str, Any]
    compensation: list[IrStep]
    evidence: list[IrEvidence]
    idempotency_key: str | None
    idempotency_window: str
    rate_limit: dict[str, int | None] | None
    mcp_exposed: bool


class IrRule(_Ir):
    when: str
    approve: str | None
    approvers: list[dict[str, Any]]
    step_up: dict[str, Any] | None
    sla: dict[str, Any] | None


class IrPolicy(_Ir):
    name: str
    separation_of_duties: bool
    rules: list[IrRule]


class IrWindow(_Ir):
    name: str
    timezone: str
    schedule: list[dict[str, Any]]
    freezes: list[dict[str, Any]]


class McpTool(_Ir):
    name: str
    kind: str
    source: str | None
    description: str
    input_schema: dict[str, Any]


class CompiledOntology(_Ir):
    ir_version: str = IR_VERSION
    name: str
    version: str
    checksum: str
    object_types: list[IrObjectType]
    link_types: list[IrLinkType]
    action_types: list[IrActionType]
    policies: list[IrPolicy]
    windows: list[IrWindow]
    tests: list[str]
    mcp_tools: list[McpTool]

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2) + "\n"

    def to_dict(self) -> dict[str, Any]:
        """The same document as :meth:`to_json`, as JSON-compatible Python values."""
        return self.model_dump(mode="json", by_alias=True)
