"""The test helper that draws a runner's three loopback ports never draws one port twice, nor the
port of the run's tools server.

A hosted step binds three loopback proxies (model, MCP, CONNECT), after the runner has bound its
tools server on `runner_settings.tools_url`, a port drawn and released by the fixture. The tests used
to draw their ports with three `free_port()` calls; each closed its socket before the next call, so
the kernel could hand the same port back, or the tools server's, and the step failed on 'address in
use' before it started, which a test then read as a missing event.

The real kernel only does so now and then, so sampling it guards nothing: `FakeKernel` reuses a
closed port every time it can, and the helper must still draw three distinct, acceptable ports.
"""

from __future__ import annotations

import socket

import pytest

from . import conftest
from .conftest import free_port, loopback_ports

FIRST = 40000


class FakeKernel:
    """The loopback as a kernel is free to run it: binding port 0 hands out the lowest port no open
    socket holds, so a port comes back as soon as its socket is closed."""

    def __init__(self) -> None:
        self.held: set[int] = set()
        self.bound = 0

    def socket(self) -> FakeSocket:
        return FakeSocket(self)


class FakeSocket:
    def __init__(self, kernel: FakeKernel) -> None:
        self.kernel = kernel
        self.port: int | None = None

    def bind(self, address: tuple[str, int]) -> None:
        assert address == ("127.0.0.1", 0), "the helpers ask for any free loopback port"
        assert self.port is None
        # A helper that closed a socket and drew again would get the same port forever: fail, never hang.
        assert self.kernel.bound < 100, "the helper drew 100 ports without settling: it loops"
        port = FIRST
        while port in self.kernel.held:
            port += 1
        self.kernel.held.add(port)
        self.kernel.bound += 1
        self.port = port

    def getsockname(self) -> tuple[str, int]:
        assert self.port is not None
        return ("127.0.0.1", self.port)

    def close(self) -> None:
        if self.port is not None:
            self.kernel.held.discard(self.port)
            self.port = None

    def __enter__(self) -> FakeSocket:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@pytest.fixture
def kernel(monkeypatch: pytest.MonkeyPatch) -> FakeKernel:
    """`conftest`'s helpers open their sockets on `FakeKernel`; nothing else does."""
    fake = FakeKernel()
    monkeypatch.setattr(conftest, "socket", fake)
    return fake


def test_the_three_ports_are_distinct_when_the_kernel_reuses_a_closed_port(kernel: FakeKernel) -> None:
    ports = loopback_ports()

    assert len(set(ports)) == 3
    assert kernel.held == set(), "every socket is closed: the ports are free for the runner"


def test_the_tools_servers_port_is_never_drawn(kernel: FakeKernel) -> None:
    tools = free_port()  # as `runner_settings` draws it, released at once
    ports = loopback_ports(avoid=[tools])

    assert tools not in ports
    assert len(set(ports)) == 3
    assert kernel.held == set()


def test_no_avoided_port_is_drawn_and_each_costs_one_socket_at_most(kernel: FakeKernel) -> None:
    avoid = {FIRST, FIRST + 1, FIRST + 3}
    ports = loopback_ports(avoid=avoid)

    assert not avoid & set(ports)
    assert len(set(ports)) == 3
    assert kernel.bound <= len(avoid) + 3
    assert kernel.held == set()


def test_on_the_real_kernel_the_ports_are_distinct_free_and_not_the_tools_servers() -> None:
    for _ in range(200):
        tools = free_port()
        ports = loopback_ports(avoid=[tools])
        assert len(set(ports)) == 3
        assert tools not in ports
        for port in ports:
            with socket.socket() as s:
                s.bind(("127.0.0.1", port))
