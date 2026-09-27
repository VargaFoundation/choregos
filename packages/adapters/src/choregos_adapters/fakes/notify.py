# SPDX-License-Identifier: Apache-2.0
"""Notifier en mémoire."""

from __future__ import annotations

from choregos_core.domain import Message


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Message]] = []
        #: Pour éprouver un notificateur ABSENT ou en panne — le cas le plus courant en vrai :
        #: un déploiement sans Slack. Le fake refusait de le simuler, donc rien ne vérifiait ce
        #: que la plateforme fait quand la notification échoue (elle tuait le ticket).
        self.panne: str | None = None

    async def send(self, channel: str, message: Message) -> None:
        if self.panne is not None:
            raise RuntimeError(self.panne)
        self.sent.append((channel, message))

    def last(self) -> Message | None:
        return self.sent[-1][1] if self.sent else None
