"""The SIEM export of the audit chains (S80-05b, ADR 0207), on SQLite and against simulated receivers.

SQLite has no trigger: the chain is written by `kits.audit_export.write_chain`, linked as the trigger links
it. What this file proves is the exporter's own behaviour (order, cursor, retry, filters, the lag), the
sinks' bodies against an httpx mock transport and a real TLS socket, and that an exported stream verifies
with nothing of the platform's code. `test_audit_export_postgres.py` adds the trigger, the lock and the RLS.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import ssl
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from sqlalchemy import select
from tranibor_api.audit_export.events import SCHEMA
from tranibor_api.audit_export.exporter import Exporter, build_sink, export_loop
from tranibor_api.audit_export.sinks import FakeSink, HttpJsonSink, SinkError, SyslogSink
from tranibor_api.config import Settings
from tranibor_api.db.models import AuditExportCursor, Organization
from tranibor_api.db.session import session_scope

from .conftest import SUITE_ON_POSTGRES
from .kits.audit_export import check_stream, write_chain

#: The chain is written by hand here, as the trigger would write it. On PostgreSQL the trigger writes it
#: (and the chain head) itself, so this file is SQLite's; `test_audit_export_postgres.py` is PostgreSQL's.
pytestmark = pytest.mark.skipif(
    SUITE_ON_POSTGRES, reason="the chain is the trigger's on PostgreSQL: test_audit_export_postgres.py"
)


@pytest.fixture
async def orgs(app: Any) -> dict[str, str]:
    async with session_scope(orgs="*") as session:
        rows = [Organization(slug=slug, name=slug.upper()) for slug in ("a", "b")]
        session.add_all(rows)
        await session.flush()
        return {row.slug: row.id for row in rows}


def as_json(sink: FakeSink) -> list[dict[str, Any]]:
    return [event.as_json() for event in sink.delivered]


async def cursors(sink: str = "siem") -> dict[str, tuple[int, str]]:
    async with session_scope(orgs="*") as session:
        rows = (
            await session.execute(select(AuditExportCursor).where(AuditExportCursor.sink == sink))
        ).scalars()
        return {row.chain: (row.seq, row.hash) for row in rows}


# ───────────────────────────── the exporter ─────────────────────────────


async def test_every_chain_is_shipped_in_order_and_verifies_at_the_receiver(orgs: dict[str, str]) -> None:
    last_a = await write_chain(orgs["a"], 5)
    await write_chain(orgs["b"], 3)
    await write_chain(None, 4)
    sink = FakeSink()
    exporter = Exporter(sink, batch=2)  # three pages for `a`: the cursor moves between them

    result = await exporter.run_pass()

    assert result.shipped == 12 and result.failed == 0
    assert sink.batches == [2, 2, 1, 2, 1, 2, 2]
    seen = check_stream(as_json(sink))
    assert seen == {orgs["a"]: 5, orgs["b"]: 3, "": 4}
    first = as_json(sink)[0]
    assert first["schema"] == SCHEMA and first["org"] == "a" and first["seq"] == 1
    assert set(first) >= {"prev_hash", "hash", "canonical", "id", "ts", "action", "payload", "actor_kind"}
    assert first["payload"] == {"n": 1, "note": "é", "ratio": 0.1}
    assert (await cursors())[orgs["a"]] == (5, last_a)


async def test_a_restart_resumes_at_the_cursor_and_ships_only_what_is_new(orgs: dict[str, str]) -> None:
    last = await write_chain(orgs["a"], 3)
    first = FakeSink()
    await Exporter(first).run_pass()
    assert len(first.delivered) == 3

    # A new process: a new exporter, a new sink object, the same cursors in the database.
    second = FakeSink()
    await Exporter(second).run_pass()
    assert second.delivered == []

    await write_chain(orgs["a"], 2, start=4, prev=last)
    await Exporter(second).run_pass()
    assert [event.seq for event in second.delivered] == [4, 5]
    assert check_stream(as_json(first) + as_json(second)) == {orgs["a"]: 5}


async def test_a_refused_batch_leaves_the_cursor_and_is_sent_again(orgs: dict[str, str]) -> None:
    await write_chain(orgs["a"], 4)
    sink = FakeSink()
    exporter = Exporter(sink, batch=2)
    sink.fail()

    result = await exporter.run_pass()

    assert result.failed == 1 and sink.delivered == []
    assert (await cursors())[orgs["a"]][0] == 0

    result = await exporter.run_pass()
    assert result.failed == 0
    assert sink.keys() == [f"{orgs['a']}:{n}" for n in (1, 2, 3, 4)]
    assert (await cursors())[orgs["a"]][0] == 4


async def test_a_lost_acknowledgement_repeats_a_batch_but_never_skips_or_reorders(
    orgs: dict[str, str],
) -> None:
    """At least once: the receiver stored a batch and the answer never came. The cursor stays, the batch goes
    again, and the receiver sees a repeat of what it has, in order, with no hole."""
    await write_chain(orgs["a"], 4)
    sink = FakeSink()
    sink.lose_ack()  # the first batch is recorded, then reported failed
    exporter = Exporter(sink, batch=2)

    await exporter.run_pass()
    await exporter.run_pass()

    assert sink.keys().count(f"{orgs['a']}:1") == 2  # the repeat
    assert check_stream(as_json(sink)) == {orgs["a"]: 4}
    assert (await cursors())[orgs["a"]][0] == 4


async def test_a_failing_chain_does_not_hold_back_the_others(orgs: dict[str, str]) -> None:
    await write_chain(orgs["a"], 2)
    await write_chain(orgs["b"], 2)

    class OneChainFails(FakeSink):
        async def send(self, events: Any) -> None:
            if events[0].org == "a":
                raise SinkError("a is refused")
            await super().send(events)

    sink = OneChainFails()
    result = await Exporter(sink).run_pass()

    assert result.failed == 1 and result.shipped == 2
    assert {event.org for event in sink.delivered} == {"b"}
    assert (await cursors())[orgs["a"]][0] == 0


async def test_the_filter_names_organisations_and_can_leave_the_platform_out(orgs: dict[str, str]) -> None:
    await write_chain(orgs["a"], 2)
    await write_chain(orgs["b"], 2)
    await write_chain(None, 2)
    sink = FakeSink()
    await Exporter(sink, orgs=["b"], platform=False).run_pass()
    assert {event.org for event in sink.delivered} == {"b"}
    assert set(await cursors()) == {orgs["b"]}

    everything = FakeSink()
    await Exporter(everything, name="other").run_pass()
    assert {event.org for event in everything.delivered} == {"a", "b", "platform"}


async def test_an_unknown_organisation_in_the_filter_is_an_error_not_a_silent_skip(
    orgs: dict[str, str],
) -> None:
    with pytest.raises(LookupError, match="nobody"):
        await Exporter(FakeSink(), orgs=["nobody"]).run_pass()


async def test_a_second_sink_name_ships_the_whole_chain_again(orgs: dict[str, str]) -> None:
    await write_chain(orgs["a"], 3)
    await Exporter(FakeSink(), name="one").run_pass()
    other = FakeSink()
    await Exporter(other, name="two").run_pass()
    assert len(other.delivered) == 3


async def test_a_cursor_that_does_not_meet_the_chain_is_logged_and_the_rest_still_ships(
    orgs: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """A prefix was removed (retention, later) or the chain was rewritten: the entries after the cursor are
    shipped with their own `prev_hash`, which tells the receiver; the platform logs that it saw."""
    await write_chain(orgs["a"], 2, start=5, prev="f" * 64)
    sink = FakeSink()
    await Exporter(sink).run_pass()
    assert [event.seq for event in sink.delivered] == [5, 6]
    assert "audit.export.chain_discontinuity" in capsys.readouterr().out


async def test_an_entry_outside_the_chain_is_not_exported(orgs: dict[str, str]) -> None:
    from tranibor_api.db.models import AuditLog

    from .kits.audit_export import T0

    async with session_scope(orgs="*") as session:
        session.add(AuditLog(org_id=orgs["a"], action="old", ts=T0, actor_kind="user"))
    sink = FakeSink()
    await Exporter(sink).run_pass()
    assert sink.delivered == []


async def test_the_loop_ships_retries_after_a_failure_and_closes_its_sink(
    orgs: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    await write_chain(orgs["a"], 2)
    closed: list[bool] = []

    class Sink(FakeSink):
        async def close(self) -> None:
            closed.append(True)

    sink = Sink()
    sink.fail(2)
    settings = Settings(audit_export=True, audit_export_sink="fake", audit_export_interval_s=1.0, fakes=True)
    waits: list[float] = []
    real_sleep = asyncio.sleep

    async def quick(seconds: float) -> None:
        waits.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr("tranibor_api.audit_export.exporter.asyncio.sleep", quick)
    task = asyncio.create_task(export_loop(settings, Exporter(sink, batch=10)))
    for _ in range(200):
        await real_sleep(0.01)
        if len(sink.delivered) == 2:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(sink.delivered) == 2
    assert waits[:2] == [2.0, 4.0]  # two failed passes: each waits twice the one before
    assert closed == [True]


# ───────────────────────────── the settings ─────────────────────────────


def test_the_export_is_off_by_default_and_its_token_never_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().audit_export is False
    monkeypatch.setenv("TRANIBOR_AUDIT_EXPORT", "true")
    monkeypatch.setenv("TRANIBOR_AUDIT_EXPORT_TOKEN", "s3cr3t-token")
    monkeypatch.setenv("TRANIBOR_AUDIT_EXPORT_ORGS", '["a"]')
    settings = Settings()
    assert settings.audit_export and settings.audit_export_orgs == ["a"]
    assert "s3cr3t-token" not in repr(settings) and "s3cr3t-token" not in settings.model_dump_json()


def test_the_fake_sink_is_refused_outside_the_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(build_sink(Settings(audit_export_sink="fake")), FakeSink)  # the suites run on the fakes
    monkeypatch.setenv("TRANIBOR_FAKES", "0")
    with pytest.raises(ValueError, match="TRANIBOR_FAKES"):
        build_sink(Settings(audit_export_sink="fake"))


def test_a_sink_that_cannot_be_built_says_why() -> None:
    with pytest.raises(ValueError, match="https://"):
        build_sink(Settings(audit_export_sink="https", audit_export_url="http://siem.example/hec"))
    with pytest.raises(ValueError, match="format"):
        build_sink(Settings(audit_export_url="https://siem.example", audit_export_format="xml"))
    with pytest.raises(ValueError, match="syslog host"):
        build_sink(Settings(audit_export_sink="syslog"))


async def test_the_api_starts_the_export_only_when_it_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from tranibor_api.main import _audit_exporter

    assert _audit_exporter(Settings(database_url="postgresql+asyncpg://x/y")) is None
    on = Settings(
        database_url="postgresql+asyncpg://x/y", audit_export=True, audit_export_sink="fake", fakes=True
    )
    assert isinstance(_audit_exporter(on), Exporter)
    sqlite = Settings(
        database_url="sqlite+aiosqlite:///x.db", audit_export=True, audit_export_sink="fake", fakes=True
    )
    assert _audit_exporter(sqlite) is None


# ───────────────────────────── the HTTPS sink ─────────────────────────────


class Receiver:
    """An httpx transport that records requests and answers what it is told."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status, self.body = status, body
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, json=self.body if self.body is not None else {})

        return httpx.MockTransport(handle)


async def batch_of(orgs: dict[str, str], count: int = 2) -> list[Any]:
    await write_chain(orgs["a"], count)
    sink = FakeSink()
    await Exporter(sink).run_pass()
    return list(sink.delivered)


async def test_the_webhook_gets_a_json_body_and_a_bearer_token(orgs: dict[str, str]) -> None:
    events = await batch_of(orgs)
    receiver = Receiver()
    sink = HttpJsonSink(
        "https://siem.example/audit", token="tok", transport=receiver.transport(), source="prod-eu"
    )
    await sink.send(events)
    [request] = receiver.requests
    assert request.headers["authorization"] == "Bearer tok"
    body = json.loads(request.content)
    assert body["source"] == "prod-eu"
    assert check_stream(body["events"]) == {orgs["a"]: 2}
    await sink.close()


async def test_splunk_hec_gets_concatenated_events_and_a_refusal_in_a_200_is_a_failure(
    orgs: dict[str, str],
) -> None:
    events = await batch_of(orgs)
    receiver = Receiver(body={"text": "Success", "code": 0})
    sink = HttpJsonSink(
        "https://splunk.example:8088/services/collector/event",
        format="splunk-hec",
        scheme="Splunk",
        token="hec-token",
        index="audit",
        transport=receiver.transport(),
    )
    await sink.send(events)
    [request] = receiver.requests
    assert request.headers["authorization"] == "Splunk hec-token"
    lines = [json.loads(line) for line in request.content.decode().split("\n")]
    assert [line["event"]["seq"] for line in lines] == [1, 2]
    assert lines[0]["index"] == "audit" and lines[0]["sourcetype"] == "choregos:audit"
    assert lines[0]["time"] == events[0].epoch
    receiver.body = {"text": "Incorrect data format", "code": 6}
    with pytest.raises(SinkError, match="Incorrect data format"):
        await sink.send(events)


async def test_elastic_bulk_names_each_document_by_its_chain_and_place(orgs: dict[str, str]) -> None:
    events = await batch_of(orgs)
    receiver = Receiver(body={"errors": False, "items": []})
    sink = HttpJsonSink(
        "https://es.example:9200",
        format="elastic-bulk",
        scheme="ApiKey",
        token="k",
        transport=receiver.transport(),
    )
    await sink.send(events)
    [request] = receiver.requests
    assert request.url.path == "/_bulk" and request.headers["authorization"] == "ApiKey k"
    assert request.headers["content-type"] == "application/x-ndjson"
    lines = [json.loads(line) for line in request.content.decode().splitlines()]
    assert lines[0] == {"index": {"_index": "choregos-audit", "_id": f"{orgs['a']}:1"}}
    assert lines[1]["@timestamp"].endswith("Z") and lines[1]["seq"] == 1
    receiver.body = {"errors": True, "items": [{"index": {"error": {"type": "mapper_parsing_exception"}}}]}
    with pytest.raises(SinkError, match="mapper_parsing_exception"):
        await sink.send(events)


async def test_a_header_without_a_scheme_sends_the_bare_token(orgs: dict[str, str]) -> None:
    receiver = Receiver()
    sink = HttpJsonSink(
        "https://siem.example/in", header="X-Api-Key", scheme="", token="abc", transport=receiver.transport()
    )
    await sink.send(await batch_of(orgs, 1))
    assert receiver.requests[0].headers["x-api-key"] == "abc"


@pytest.mark.parametrize("status", [401, 429, 500, 503])
async def test_an_error_status_is_a_failure_that_names_the_status(orgs: dict[str, str], status: int) -> None:
    sink = HttpJsonSink("https://siem.example/in", transport=Receiver(status=status).transport())
    with pytest.raises(SinkError, match=str(status)):
        await sink.send(await batch_of(orgs, 1))


async def test_a_network_error_is_a_failure(orgs: dict[str, str]) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    sink = HttpJsonSink("https://siem.example/in", transport=httpx.MockTransport(refuse))
    with pytest.raises(SinkError, match="ConnectError"):
        await sink.send(await batch_of(orgs, 1))


async def test_a_redirect_is_not_followed_with_the_token(orgs: dict[str, str]) -> None:
    seen: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(307, headers={"Location": "https://elsewhere.example/steal"})

    sink = HttpJsonSink("https://siem.example/in", token="tok", transport=httpx.MockTransport(redirect))
    with pytest.raises(SinkError, match="307"):
        await sink.send(await batch_of(orgs, 1))
    # The Location is real: a client that followed it would have sent the token to the other host.
    assert [request.url.host for request in seen] == ["siem.example"]


# ───────────────────────────── the syslog sink, over a real TLS socket ─────────────────────────────


def self_signed(directory: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = directory / "cert.pem", directory / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return cert_file, key_file


class SyslogServer:
    """A TLS listener that reads RFC 5425 frames (octet counting)."""

    def __init__(self) -> None:
        self.frames: list[str] = []
        self.done = asyncio.Event()

    async def start(self, directory: Path) -> int:
        self.cert, key = self_signed(directory)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, key)
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0, ssl=context)
        return int(self.server.sockets[0].getsockname()[1])

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        data = await reader.read()
        while data:
            length, _, rest = data.partition(b" ")
            self.frames.append(rest[: int(length)].decode())
            data = rest[int(length) :]
        writer.close()
        self.done.set()

    async def stop(self) -> None:
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
async def syslog(tmp_path: Path) -> Any:
    server = SyslogServer()
    port = await server.start(tmp_path)
    yield server, port
    await server.stop()


async def test_syslog_sends_rfc5424_frames_over_tls_that_verify(orgs: dict[str, str], syslog: Any) -> None:
    server, port = syslog
    events = await batch_of(orgs, 3)
    sink = SyslogSink("localhost", port, ca_file=str(server.cert), hostname="api-0")

    await sink.send(events)
    await asyncio.wait_for(server.done.wait(), 5)

    assert len(server.frames) == 3
    first = server.frames[0]
    # PRI VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [SD] MSG
    assert first.startswith(
        "<110>1 2026-11-20T08:00:01.250000Z api-0 choregos-audit - AUDIT [choregos-audit@32473 "
    )
    header, _, message = first.partition("] ")
    assert f'org="a" seq="1" prev_hash="{"0" * 64}"' in header
    assert check_stream([json.loads(frame.partition("] ")[2]) for frame in server.frames]) == {orgs["a"]: 3}
    assert json.loads(message)["hash"] in header


async def test_syslog_in_cef_carries_the_chain_in_labelled_fields(orgs: dict[str, str], syslog: Any) -> None:
    server, port = syslog
    events = await batch_of(orgs, 1)
    sink = SyslogSink("localhost", port, format="cef", ca_file=str(server.cert), hostname="api-0")
    await sink.send(events)
    await asyncio.wait_for(server.done.wait(), 5)
    cef = server.frames[0].partition("] ")[2]
    assert cef.startswith("CEF:0|Choregos|Choregos|")
    assert "|thing.1|thing.1|3|" in cef
    assert "cn1Label=seq cn1=1" in cef and f"cs3Label=hash cs3={events[0].hash}" in cef
    assert f"cs2Label=prevHash cs2={'0' * 64}" in cef and "suser=u-1" in cef


def test_cef_escapes_what_would_break_a_field(orgs: dict[str, str]) -> None:
    from dataclasses import replace

    from tranibor_api.audit_chain import Link
    from tranibor_api.audit_export.events import ExportEvent

    from .kits.audit_export import T0

    link = Link(
        "i", None, 1, T0, "user", "a=b\\c", "x|y", None, None, '{"k": "line\\nbreak"}', "0" * 64, "1" * 64
    )
    event = ExportEvent(link, 1, "0" * 64, "1" * 64, "platform")
    cef = event.as_cef("1.0")
    assert "|x\\|y|x\\|y|3|" in cef and "suser=a\\=b\\\\c" in cef
    assert "\n" not in cef
    quoted = replace(event, link=replace(link, actor_id='x"] y'))
    assert 'actor_id="x\\"\\] y"' in quoted.structured_data("sd@1")


async def test_syslog_refuses_a_server_that_the_ca_does_not_vouch_for(
    orgs: dict[str, str], syslog: Any
) -> None:
    _, port = syslog
    sink = SyslogSink("localhost", port, hostname="api-0")  # the system's roots: the self-signed cert fails
    with pytest.raises(SinkError, match=r"SSL|certificate|CERTIFICATE"):
        await sink.send(await batch_of(orgs, 1))


async def test_syslog_to_a_closed_port_is_a_failure(orgs: dict[str, str]) -> None:
    sink = SyslogSink("127.0.0.1", 1, hostname="api-0")
    with pytest.raises(SinkError):
        await sink.send(await batch_of(orgs, 1))


# ───────────────────────────── the metrics ─────────────────────────────


async def test_the_lag_is_the_distance_between_the_heads_and_the_cursors(orgs: dict[str, str]) -> None:
    from tranibor_api import metrics

    await write_chain(orgs["a"], 5)
    await write_chain(None, 3)
    sink = FakeSink()
    exporter = Exporter(sink, batch=2)
    sink.fail(2)  # `a`'s first batch is refused, and the platform's: nothing moved yet
    await exporter.run_pass()
    await metrics.refresh()
    text = metrics.expose().decode()
    assert 'choregos_audit_export_lag_entries{sink="siem"} 8.0' in text

    await exporter.run_pass()
    await metrics.refresh()
    text = metrics.expose().decode()
    assert 'choregos_audit_export_lag_entries{sink="siem"} 0.0' in text
    assert 'choregos_audit_export_progress_timestamp_seconds{sink="siem"}' in text
    assert 'choregos_audit_export_failures_total{sink="fake"}' in text
    assert 'choregos_audit_export_events_total{sink="fake"}' in text
