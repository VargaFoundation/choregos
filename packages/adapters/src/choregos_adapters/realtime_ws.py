# SPDX-License-Identifier: Apache-2.0
"""A `RealtimeConnector` over `websockets` (ADR 0080 §10): the gateway's realtime socket for a voice
session's relay, the container's `/invocations_ws` for a hosted session's pod (S41-21).

`websockets` is imported when a socket opens: a process that never opens one does not need it.
"""

from __future__ import annotations

from typing import Any

from .base import RealtimeClosed, RealtimeLink


class WebsocketsConnector:
    """Opens a text WebSocket; frames larger than `max_size` bytes close it."""

    def __init__(self, max_size: int = 8 * 1024 * 1024) -> None:
        self.max_size = max_size

    async def connect(self, url: str, headers: dict[str, str]) -> RealtimeLink:
        from websockets.asyncio.client import connect

        socket = await connect(url, additional_headers=headers, max_size=self.max_size)
        return WebsocketsLink(socket)


class WebsocketsLink:
    def __init__(self, socket: Any) -> None:
        self._socket = socket

    async def send(self, message: str) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            await self._socket.send(message)
        except ConnectionClosed as closed:
            raise RealtimeClosed(closed.rcvd.code if closed.rcvd else 1006) from closed

    async def recv(self) -> str:
        from websockets.exceptions import ConnectionClosed

        try:
            message = await self._socket.recv()
        except ConnectionClosed as closed:
            code, reason = (closed.rcvd.code, closed.rcvd.reason) if closed.rcvd else (1006, "")
            raise RealtimeClosed(code, reason) from closed
        return message if isinstance(message, str) else message.decode("utf-8", "replace")

    async def close(self) -> None:
        await self._socket.close()


__all__ = ["WebsocketsConnector", "WebsocketsLink"]
