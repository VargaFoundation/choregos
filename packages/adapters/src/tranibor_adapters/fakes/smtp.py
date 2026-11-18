# SPDX-License-Identifier: Apache-2.0
"""An in-process SMTP server that records what it receives (no TLS, no auth)."""

from __future__ import annotations

import asyncio


class FakeSmtpServer:
    def __init__(self) -> None:
        #: (sender, recipients, raw message data)
        self.messages: list[tuple[str, list[str], bytes]] = []
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return int(self._server.sockets[0].getsockname()[1])

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async def reply(text: str) -> None:
            writer.write(text.encode() + b"\r\n")
            await writer.drain()

        sender = ""
        recipients: list[str] = []
        try:
            await reply("220 fake-smtp ready")
            while line := await reader.readline():
                text = line.decode(errors="replace").strip()
                verb = text.upper()
                if verb.startswith("EHLO"):
                    await reply("250-fake-smtp")
                    await reply("250 8BITMIME")
                elif verb.startswith("HELO"):
                    await reply("250 fake-smtp")
                elif verb.startswith("MAIL FROM"):
                    sender = _address(text)
                    recipients = []
                    await reply("250 OK")
                elif verb.startswith("RCPT TO"):
                    recipients.append(_address(text))
                    await reply("250 OK")
                elif verb == "DATA":
                    await reply("354 End data with <CR><LF>.<CR><LF>")
                    data = bytearray()
                    while chunk := await reader.readline():
                        if chunk in (b".\r\n", b".\n"):
                            break
                        data += chunk[1:] if chunk.startswith(b"..") else chunk
                    self.messages.append((sender, recipients, bytes(data)))
                    sender, recipients = "", []
                    await reply("250 OK queued")
                elif verb == "RSET":
                    sender, recipients = "", []
                    await reply("250 OK")
                elif verb == "NOOP":
                    await reply("250 OK")
                elif verb == "QUIT":
                    await reply("221 bye")
                    break
                else:
                    await reply("502 command not implemented")
        finally:
            writer.close()


def _address(command: str) -> str:
    """`MAIL FROM:<a@b>` -> `a@b`."""
    start, end = command.find("<"), command.rfind(">")
    return command[start + 1 : end] if 0 <= start < end else command.split(":", 1)[-1].strip()
