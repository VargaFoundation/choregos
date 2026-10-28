"""The test helper that draws a runner's three loopback ports never draws one port twice.

A hosted step binds three loopback proxies (model, MCP, CONNECT). The tests used to draw their
ports with three `free_port()` calls; each closed its socket before the next call, so the kernel
could hand the same port back and the step failed on 'address in use' before it started, which a
test then read as a missing event.
"""

from __future__ import annotations

import socket

from .conftest import loopback_ports


def test_the_three_ports_are_distinct_and_free() -> None:
    for _ in range(200):
        ports = loopback_ports()
        assert len(set(ports)) == 3
        for port in ports:
            with socket.socket() as s:
                s.bind(("127.0.0.1", port))
