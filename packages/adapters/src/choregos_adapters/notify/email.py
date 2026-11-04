# SPDX-License-Identifier: Apache-2.0
"""E-mail notifier: plain-text messages sent through an SMTP relay (stdlib only)."""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from choregos_core.domain import Message

from ..errors import UpstreamError


class EmailNotifier:
    """Sends a notification as an e-mail. The `channel` names the recipients, comma-separated."""

    channel_kind = "email"

    def __init__(  # noqa: PLR0913 - the SMTP connection is described by its own keyword-only settings
        self,
        *,
        host: str,
        port: int = 587,
        username: str = "",
        password: str = "",
        from_address: str,
        to: list[str] | None = None,
        starttls: bool = True,
        use_ssl: bool = False,
        timeout_s: float = 15.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_address = from_address
        self.to = list(to or [])
        self.starttls = starttls
        self.use_ssl = use_ssl
        self.timeout_s = timeout_s

    def _recipients(self, channel: str) -> list[str]:
        named = [part.strip() for part in channel.split(",") if "@" in part]
        return named or list(self.to)

    async def send(self, channel: str, message: Message) -> None:
        recipients = self._recipients(channel)
        if not recipients:
            raise UpstreamError(
                "email", "no recipient: set notify.emails on the project or `to` on the connector"
            )
        msg = EmailMessage()
        msg["Subject"] = "[Choregos] " + message.title
        msg["From"] = self.from_address
        msg["To"] = ", ".join(recipients)
        lines = [message.body] if message.body else []
        lines.extend(f"{key}: {value}" for key, value in message.context.items())
        if message.url:
            lines.append(message.url)
        msg.set_content("\n\n".join(lines) if lines else message.title)
        try:
            await asyncio.to_thread(self._deliver, msg, recipients)
        except (OSError, smtplib.SMTPException) as exc:
            raise UpstreamError("email", f"SMTP delivery to {self.host}:{self.port} failed: {exc}") from exc

    def _deliver(self, msg: EmailMessage, recipients: list[str]) -> None:
        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout_s) as smtp:
                self._login_and_send(smtp, msg, recipients)
            return
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout_s) as smtp:
            if self.starttls:
                smtp.starttls()
            self._login_and_send(smtp, msg, recipients)

    def _login_and_send(self, smtp: smtplib.SMTP, msg: EmailMessage, recipients: list[str]) -> None:
        if self.username:
            smtp.login(self.username, self.password)
        smtp.send_message(msg, from_addr=self.from_address, to_addrs=recipients)
