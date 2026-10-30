# SPDX-License-Identifier: Apache-2.0
"""In-memory notifier."""

from __future__ import annotations

from choregos_core.domain import Message


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Message]] = []
        #: To exercise an ABSENT or failing notifier — the most common case for real: a deployment
        #: without Slack. The fake refused to simulate it, so nothing checked what the platform does
        #: when the notification fails (it killed the ticket).
        self.failure: str | None = None

    async def send(self, channel: str, message: Message) -> None:
        if self.failure is not None:
            raise RuntimeError(self.failure)
        self.sent.append((channel, message))

    @property
    def panne(self) -> str | None:  # lang: fr-ok alias:internal
        return self.failure

    @panne.setter
    def panne(self, value: str | None) -> None:  # lang: fr-ok alias:internal
        self.failure = value

    def last(self) -> Message | None:
        return self.sent[-1][1] if self.sent else None
