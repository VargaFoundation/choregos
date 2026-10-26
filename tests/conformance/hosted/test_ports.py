"""The ports the hosted conformance hands an agent stay its own until it binds them (finding #930).

`free_port` used to bind port 0, close the probe and return the number: the kernel was then free to
give the same port to the next probe (four are drawn per agent) or to any outbound connection, and
the Node reference lost its port in CI (`Server.setupListenHandle`, readiness timeout).
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from .harness import free_port, published_port


def _ephemeral_range() -> tuple[int, int]:
    low, high = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()
    return int(low), int(high)


def test_no_port_is_handed_out_twice() -> None:
    drawn = [free_port() for _ in range(200)] + [published_port() for _ in range(50)]
    assert len(drawn) == len(set(drawn))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads the Linux ephemeral range")
def test_a_port_is_never_one_the_kernel_hands_to_outbound_connections() -> None:
    low, high = _ephemeral_range()
    assert not [p for p in (free_port() for _ in range(50)) if low <= p <= high]


def test_a_port_already_bound_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    from . import harness

    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        taken = holder.getsockname()[1]
        candidates = iter([taken, 23456, 23457, 23458])
        monkeypatch.setattr(harness.random, "randint", lambda *_: next(candidates))
        assert free_port() != taken
