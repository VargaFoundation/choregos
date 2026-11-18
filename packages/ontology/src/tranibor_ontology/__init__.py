# SPDX-License-Identifier: Apache-2.0
"""Trial of the platform core (phase 0, TRV-001): an ontology package in YAML, validated with stable
codes, compiled into an engine-independent IR, and the MCP tools generated from it.

Trial code: it may be thrown away. What counts is the evidence and the measured effort (TRV-010).
"""

from tranibor_ontology.compiler import compile_directory, compile_package
from tranibor_ontology.errors import Issue, OntologyError, Validation
from tranibor_ontology.loader import Package, Resource, load_package
from tranibor_ontology.validator import Registry, validate

__all__ = [
    "Issue",
    "OntologyError",
    "Package",
    "Registry",
    "Resource",
    "Validation",
    "compile_directory",
    "compile_package",
    "load_package",
    "validate",
]
