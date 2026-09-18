"""Notifier en mémoire."""

from __future__ import annotations

from choregos_core.domain import Message


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Message]] = []

    async def send(self, channel: str, message: Message) -> None:
        self.sent.append((channel, message))

    def last(self) -> Message | None:
        return self.sent[-1][1] if self.sent else None
