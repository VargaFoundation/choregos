# SPDX-License-Identifier: Apache-2.0
"""The extension of the `software-delivery` plugin (ADR 0181, round C2): what its trade needs inside the
platform's processes, registered through the core's seams when the API and the worker load the
`choregos.plugins` entry point.

- the twelve gates that read a diff, a pipeline or a pull request (`scope_respected`, `diff_size_max`,
  `no_secrets`, `markdown_sections`, `adr_number_free`, `docs_updated`, `coverage_delta_min`,
  `ci_green`, `review_approved`, `scans_ok`, `provenance_signed`, `already_fixed_proven`), under the
  names stored workflows carry (`gates.py`);
- the context provider `delivery` their facts come from, and its `judged` hook (`facts.py`).

`register` is idempotent: the API loads the extensions twice (through the worker it imports, then in
`create_app`).
"""

from __future__ import annotations

from tranibor_core.gates import context_provider

from .facts import judged, read_delivery_facts
from .gates import PROVIDER, register_gates
from .names import NAME


def register() -> None:
    """The `choregos.plugins` entry point of `tranibor-software-delivery`."""
    context_provider(PROVIDER, plugin=NAME, judged=judged)(read_delivery_facts)
    register_gates()
