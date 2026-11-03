# SPDX-License-Identifier: Apache-2.0
"""The e-mail notifier delivers through SMTP, to the recipients its channel names."""

from __future__ import annotations

from collections.abc import AsyncIterator
from email import message_from_bytes

import pytest
from choregos_adapters.errors import UpstreamError
from choregos_adapters.fakes.smtp import FakeSmtpServer
from choregos_adapters.notify.email import EmailNotifier
from choregos_adapters.registry import spec_of
from choregos_core.domain import Message


@pytest.fixture
async def smtp() -> AsyncIterator[tuple[FakeSmtpServer, int]]:
    server = FakeSmtpServer()
    port = await server.start()
    try:
        yield server, port
    finally:
        await server.stop()


def _notifier(port: int, to: list[str] | None) -> EmailNotifier:
    return EmailNotifier(host="127.0.0.1", port=port, from_address="choregos@acme.dev", to=to, starttls=False)


async def test_a_message_reaches_the_smtp_server(smtp: tuple[FakeSmtpServer, int]) -> None:
    server, port = smtp
    await _notifier(port, ["po@acme.dev"]).send(
        "#choregos", Message(title="Train departed", body="Release 1.2", url="https://c/x")
    )
    assert len(server.messages) == 1
    sender, recipients, data = server.messages[0]
    assert sender == "choregos@acme.dev"
    assert recipients == ["po@acme.dev"]
    parsed = message_from_bytes(data)
    assert parsed["Subject"] == "[Choregos] Train departed"
    assert "https://c/x" in str(parsed.get_payload())


async def test_a_channel_naming_addresses_overrides_the_default(smtp: tuple[FakeSmtpServer, int]) -> None:
    server, port = smtp
    await _notifier(port, ["po@acme.dev"]).send("a@x.dev,b@y.dev", Message(title="T", body="b"))
    assert server.messages[0][1] == ["a@x.dev", "b@y.dev"]


async def test_without_a_recipient_the_send_raises(smtp: tuple[FakeSmtpServer, int]) -> None:
    server, port = smtp
    with pytest.raises(UpstreamError, match="no recipient"):
        await _notifier(port, None).send("#choregos", Message(title="T", body="b"))
    assert server.messages == []


def test_the_email_type_is_registered_with_a_secret_password() -> None:
    spec = spec_of("notify", "email")
    assert spec is not None
    assert spec.secret_fields == ("password",)
    assert spec.egress({"host": "smtp.acme.dev", "port": 465}) == [("smtp.acme.dev", 465)]
    assert spec.egress({"host": "smtp.acme.dev"}) == [("smtp.acme.dev", 587)]
