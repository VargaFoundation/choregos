# SPDX-License-Identifier: Apache-2.0
"""Who writes which lines of a run's journal (ADR 0173).

A run's journal has two authors. The run writes what it did: its ACP log, what its runner mounted,
refused or stored, the calls of its tools. The platform writes what it decided about the run: the
verdict of each guarantee (`gate.outcome`), the memories it left out (`memory.skipped`). Both land
in the same table and are read on the same page, and the console's guarantees panel reads the
platform's lines as the platform's word.

So the two are told apart by their type, in one place, here:

- a type under one of `PLATFORM_PREFIXES` is the platform's, and only the platform writes it;
- the run's own door (`POST /internal/runs/{id}/events`) refuses a batch that carries one
  (`platform_written`): a run that could write `gate.outcome` would be writing its own verdicts.

A line the platform adds to a run's journal takes its type from this module, so that a new one cannot
be written outside the reserved namespace without a test saying so
(`tests/architecture/test_run_journal_authors.py`).
"""

from __future__ import annotations

#: The verdict of one guarantee on the run, as the orchestrator judged it (ADR 0171 for its commit).
GATE_OUTCOME = "gate.outcome"

#: A memory the platform left out of the run's context, and why.
MEMORY_SKIPPED = "memory.skipped"

#: The types the platform writes in a run's journal.
PLATFORM_EVENTS: frozenset[str] = frozenset({GATE_OUTCOME, MEMORY_SKIPPED})

#: The namespaces those types live in. Reserved whole, not type by type: a line the platform adds
#: later under one of them is refused from a run before anyone thinks of it.
PLATFORM_PREFIXES: tuple[str, ...] = ("gate.", "memory.")


def platform_written(event_type: str) -> bool:
    """Whether a journal line of this type is the platform's to write, never a run's.

    Read as a reader would match it at its most lenient: surrounding blanks and case do not make a
    reserved type someone else's.
    """
    return event_type.strip().lower().startswith(PLATFORM_PREFIXES)


__all__ = ["GATE_OUTCOME", "MEMORY_SKIPPED", "PLATFORM_EVENTS", "PLATFORM_PREFIXES", "platform_written"]
