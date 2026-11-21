# SPDX-License-Identifier: Apache-2.0
"""Where the audit entries go (ADR 0207): a `Sink` takes a batch of events, in order, and either accepts
all of them or raises `SinkError`. The exporter moves its cursor only after `send` returned.

- `HttpJsonSink`: one HTTPS POST per batch, a token in a header. Three bodies, chosen by `format`:
  `webhook` (`{"source", "events": [...]}`), `splunk-hec` (concatenated `{"time", "host", "event"}` objects
  as Splunk's HTTP Event Collector reads them) and `elastic-bulk` (the `_bulk` NDJSON of Elasticsearch and
  OpenSearch, with the entry's `<chain>:<seq>` as the document's `_id`, so a second delivery overwrites the
  first instead of doubling it). A plain `http://` URL is refused: the log is not sent in clear.
- `SyslogSink`: RFC 5424 messages over TLS, framed by octet counting as RFC 5425 says, optionally in CEF.
  TCP gives no acknowledgement of the receiver's: a batch is "accepted" when it was written and flushed, so
  a message in flight when the connection breaks can be lost. The chain shows it (a missing `seq`).
- `FakeSink`: in memory and scriptable, for the tests and for `TRANIBOR_FAKES=1`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import ssl
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from tranibor_core import utcnow

from .. import __version__
from ..audit_chain import timestamp
from .events import ExportEvent

#: RFC 5424 PRI: facility 13 ("log audit") and severity 6 ("informational").
PRI = 13 * 8 + 6
#: The structured-data ID: `name@<private enterprise number>`. 32473 is the number RFC 5612 reserves for
#: documentation; an operator that holds its own sets `sd_id`.
SD_ID = "choregos-audit@32473"
TIMEOUT_S = 15.0


class SinkError(Exception):
    """The sink did not accept the batch: nothing of it is to be counted as delivered."""


class Sink(Protocol):
    #: What the metrics and the logs call it.
    kind: str

    async def send(self, events: Sequence[ExportEvent]) -> None: ...

    async def close(self) -> None: ...


@dataclass
class FakeSink:
    """Keeps every accepted event in order. `fail(n)` makes the next `n` batches fail; `lose_ack` makes the
    next batch be recorded and then fail, as a receiver that stored a batch and timed out does."""

    kind: str = "fake"
    delivered: list[ExportEvent] = field(default_factory=list)
    batches: list[int] = field(default_factory=list)
    failures: int = 0
    lost_acks: int = 0

    def fail(self, times: int = 1) -> None:
        self.failures += times

    def lose_ack(self, times: int = 1) -> None:
        self.lost_acks += times

    async def send(self, events: Sequence[ExportEvent]) -> None:
        if self.failures:
            self.failures -= 1
            raise SinkError("the fake sink was told to fail")
        self.delivered.extend(events)
        self.batches.append(len(events))
        if self.lost_acks:
            self.lost_acks -= 1
            raise SinkError("the fake sink stored the batch and its acknowledgement was lost")

    async def close(self) -> None:
        return None

    def keys(self) -> list[str]:
        return [event.key for event in self.delivered]


def _auth(header: str, scheme: str, token: str) -> dict[str, str]:
    if not token:
        return {}
    return {header: f"{scheme} {token}" if scheme else token}


class HttpJsonSink:
    """One POST per batch to an HTTPS endpoint; a 2xx the receiver's own verdict agrees with is an accept."""

    kind = "https"
    FORMATS = ("webhook", "splunk-hec", "elastic-bulk")

    def __init__(  # noqa: PLR0913 - one keyword per setting of the sink
        self,
        url: str,
        *,
        format: str = "webhook",
        token: str = "",
        header: str = "Authorization",
        scheme: str = "Bearer",
        index: str = "",
        ca_file: str = "",
        source: str = "choregos",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if format not in self.FORMATS:
            raise ValueError(f"audit export: format {format!r} is not one of {', '.join(self.FORMATS)}")
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("audit export: the URL must be https:// (the log is not sent in clear)")
        if format == "elastic-bulk" and not parts.path.rstrip("/").endswith("_bulk"):
            url = url.rstrip("/") + "/_bulk"
        self.url = url
        self.format = format
        self.index = index
        self.source = source
        self._headers = {**_auth(header, scheme, token)}
        self._client = httpx.AsyncClient(
            timeout=TIMEOUT_S,
            verify=ca_file or True,
            transport=transport,
            follow_redirects=False,
        )

    def _body(self, events: Sequence[ExportEvent]) -> tuple[str, str]:
        """`(content type, body)`."""
        if self.format == "webhook":
            body = {"source": self.source, "events": [event.as_json() for event in events]}
            return "application/json", json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        if self.format == "splunk-hec":
            lines = []
            for event in events:
                entry: dict[str, Any] = {
                    "time": event.epoch,
                    "host": socket.gethostname(),
                    "source": self.source,
                    "sourcetype": "choregos:audit",
                    "event": event.as_json(),
                }
                if self.index:
                    entry["index"] = self.index
                lines.append(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
            return "application/json", "\n".join(lines)
        lines = []
        for event in events:
            action = {"index": {"_index": self.index or "choregos-audit", "_id": event.key}}
            document = {"@timestamp": timestamp(event.moment), **event.as_json()}
            lines.append(json.dumps(action, separators=(",", ":")))
            lines.append(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
        return "application/x-ndjson", "\n".join(lines) + "\n"

    async def send(self, events: Sequence[ExportEvent]) -> None:
        content_type, body = self._body(events)
        try:
            response = await self._client.post(
                self.url,
                content=body.encode("utf-8"),
                headers={"Content-Type": content_type, **self._headers},
            )
        except httpx.HTTPError as error:
            raise SinkError(f"{type(error).__name__}: {error}"[:300]) from error
        if not response.is_success:
            raise SinkError(f"the receiver answered {response.status_code}: {response.text[:200]}")
        self._read_verdict(response)

    def _read_verdict(self, response: httpx.Response) -> None:
        """A 2xx that says it refused: Splunk's `code` other than 0, Elastic's `errors: true`."""
        if self.format not in ("splunk-hec", "elastic-bulk"):
            return
        try:
            answer = response.json()
        except ValueError:
            return
        if not isinstance(answer, dict):
            return
        if self.format == "splunk-hec" and answer.get("code", 0) != 0:
            raise SinkError(f"Splunk refused the batch: {answer.get('text')} (code {answer.get('code')})")
        if self.format == "elastic-bulk" and answer.get("errors"):
            failed = next(
                (
                    item.get("index", {}).get("error")
                    for item in answer.get("items", [])
                    if isinstance(item, dict) and item.get("index", {}).get("error")
                ),
                None,
            )
            raise SinkError(f"the bulk request had errors: {json.dumps(failed)[:200]}")

    async def close(self) -> None:
        await self._client.aclose()


class SyslogSink:
    """RFC 5424 over TLS (RFC 5425 framing). A connection per batch: a batch is rare and a TLS handshake
    is cheap beside a ten-second pass, and no half-dead connection is ever reused."""

    kind = "syslog"
    FORMATS = ("rfc5424", "cef")

    def __init__(
        self,
        host: str,
        port: int = 6514,
        *,
        format: str = "rfc5424",
        ca_file: str = "",
        hostname: str | None = None,
        sd_id: str = SD_ID,
        context: ssl.SSLContext | None = None,
        clock: Callable[[], Any] = utcnow,
    ) -> None:
        if format not in self.FORMATS:
            raise ValueError(f"audit export: format {format!r} is not one of {', '.join(self.FORMATS)}")
        if not host:
            raise ValueError("audit export: the syslog host is empty")
        self.host = host
        self.port = port
        self.format = format
        self.sd_id = sd_id
        self.hostname = hostname or _hostname()
        self._clock = clock
        self._context = context or ssl.create_default_context(cafile=ca_file or None)

    def frame(self, event: ExportEvent) -> bytes:
        """One event as one RFC 5425 frame: `<length> <message>`, the length in octets."""
        text = event.as_cef(__version__) if self.format == "cef" else event.message()
        stamp = timestamp(event.moment)
        # RFC 5424: PRI VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID STRUCTURED-DATA MSG
        header = f"<{PRI}>1 {stamp} {self.hostname} choregos-audit - AUDIT"
        message = f"{header} {event.structured_data(self.sd_id)} {text}".encode()
        return f"{len(message)} ".encode() + message

    async def send(self, events: Sequence[ExportEvent]) -> None:
        payload = b"".join(self.frame(event) for event in events)
        writer: asyncio.StreamWriter | None = None
        try:
            _, opened = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, ssl=self._context, server_hostname=self.host),
                TIMEOUT_S,
            )
            writer = opened
            opened.write(payload)
            await asyncio.wait_for(opened.drain(), TIMEOUT_S)
        except (OSError, ssl.SSLError, TimeoutError) as error:
            raise SinkError(f"{type(error).__name__}: {error}"[:300]) from error
        finally:
            if writer is not None:
                writer.close()
                # The batch was flushed: a close that breaks changes nothing.
                with contextlib.suppress(OSError, ssl.SSLError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), TIMEOUT_S)

    async def close(self) -> None:
        return None


def _hostname() -> str:
    return socket.gethostname() or "-"
