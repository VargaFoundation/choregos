"""Which lines of a run's journal are the platform's to write (S65-08, ADR 0173)."""

from __future__ import annotations

import pytest
from choregos_core.run_journal import (
    GATE_OUTCOME,
    MEMORY_SKIPPED,
    PLATFORM_EVENTS,
    PLATFORM_PREFIXES,
    platform_written,
)


def test_every_type_the_platform_writes_is_in_a_reserved_namespace() -> None:
    assert {GATE_OUTCOME, MEMORY_SKIPPED} == PLATFORM_EVENTS
    assert all(platform_written(name) for name in PLATFORM_EVENTS)
    assert all(prefix.endswith(".") and prefix == prefix.lower() for prefix in PLATFORM_PREFIXES)


RESERVED = ["gate.outcome", "gate.later", "memory.skipped", "  Gate.Outcome", "MEMORY.X"]
OWN = ["session/update", "run.result", "gateway", "gated.release", "memoryless", "gate", "", "x.gate.outcome"]


@pytest.mark.parametrize("name", RESERVED)
def test_a_reserved_type_is_the_platforms_however_it_is_spelt(name: str) -> None:
    assert platform_written(name)


@pytest.mark.parametrize("name", OWN)
def test_a_runs_own_type_is_not_reserved(name: str) -> None:
    assert not platform_written(name)
