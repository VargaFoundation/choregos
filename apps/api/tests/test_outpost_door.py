"""The outposts' door: `enrol`, `poll`, `answer`, `refuse` (S65-12, ADR 0148).

The door an outpost's daemon dials, as `packages/contracts/outpost-door.yaml` describes it. These tests
play a daemon against the mounted door (`kits.outpost_daemon`): a request is authenticated by a
signature that binds it, and is answered for the first check that fails, in the contract's order; an
instance holds one poll at a time; a call is handed over sealed to the instance and signed by the
door; an answer is taken once, from the instance that claimed the call, under the epoch of the claim.

They run on SQLite and, with `CHOREGOS_TEST_SUITE_ON_POSTGRES`, again on PostgreSQL under row-level
security. What only PostgreSQL can show is in `test_outpost_rendezvous_postgres.py` (two claimers)
and `test_outpost_long_poll_holds_no_connection.py` (the pool, the relay).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from choregos_contracts import MAX_BODY_BYTES, NONCE_HEADER, SIGNATURE_HEADER, outpost
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient

from .kits import outpost_daemon as daemon_kit
from .kits import outposts as kit
from .kits.outpost_daemon import ENROL, POLL, SLUG, Daemon, answer_path, compact, refuse_path, refused

UNKNOWN_CALL = "8f0e6b1c-5a3d-4c57-9e21-7b2a4d9c0f13"


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    """Outposts on, the door given its origin and its key, the deployment its key wrapper: before the
    application is built, since the door is mounted at start-up or not at all."""
    daemon_kit.key_wrapper(monkeypatch)
    return kit.switch_on(monkeypatch)


@pytest.fixture
async def daemon(client: AsyncClient, admin: str) -> Daemon:
    """An instance a person approved, at the outpost `factory-floor`."""
    return await daemon_kit.approved(client)


@pytest.fixture
def quick_looks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A poll that waits looks again at once: on SQLite nothing wakes it (ADR 0148, consequences)."""
    from choregos_api.outposts import rendezvous

    monkeypatch.setattr(rendezvous, "LOOK_S", (0.02, 0.05))


def _settings() -> Any:
    from choregos_api.config import get_settings

    return get_settings()


def _nonce(age_s: float = 0) -> str:
    from choregos_api.outposts import auth

    return auth.nonces(_settings()).issue(at=time.time() - age_s)


async def _poll_waits(daemon: Daemon) -> None:
    """Until the daemon's poll holds the instance's slot and waits."""
    for _ in range(400):
        if (await daemon_kit.instance_row(str(daemon.instance_id))).poll_holder is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the poll never took its slot")


async def _cut_short(poll: asyncio.Task[Any], daemon: Daemon) -> None:
    """Cancel a poll that waits, as a server that shuts down does, and see its slot given back."""
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
        await poll
    for _ in range(400):
        if (await daemon_kit.instance_row(str(daemon.instance_id))).poll_holder is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the slot of a poll that was cut short was never given back")


async def _door_events() -> list[str]:
    """Every audit event of the outposts so far, by action."""
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        rows = await session.execute(select(AuditLog.action).where(AuditLog.action.like("outpost.%")))
        return sorted(str(action) for action in rows.scalars())


# ───────────────────────── mounted, or not there at all ─────────────────────────


async def test_the_door_is_not_mounted_while_outposts_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CHOREGOS_OUTPOSTS` is the switch that stops every outpost at once (ADR 0147 §12): off, a daemon
    meets a 404 that is none of the contract's problems."""
    from choregos_api.main import create_app

    kit.configure(monkeypatch, outposts="off")
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as http:
        for path in (ENROL, POLL, answer_path(UNKNOWN_CALL), refuse_path(UNKNOWN_CALL)):
            response = await http.post(path, content=b"{}")
            assert response.status_code == 404, path
            assert "code" not in response.json() and "server_time" not in response.json()


@pytest.mark.parametrize("missing", ["outposts_door_url", "outposts_door_signing_key"])
def test_a_door_switched_on_without_its_origin_or_its_key_stops_the_start_up(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """A door that could not compare a signature's URL, or sign a call, is refused when the API
    starts, naming the setting, rather than found one refusal at a time by whoever runs an outpost."""
    from choregos_api.main import create_app

    kit.configure(monkeypatch, **{missing: None})
    with pytest.raises(RuntimeError, match=f"CHOREGOS_{missing.upper()}"):
        create_app()


async def test_the_door_is_mounted_at_the_root_and_outside_the_api_document(
    app: Any, client: AsyncClient
) -> None:
    document = app.openapi()
    assert not [path for path in document["paths"] if "/outposts/v1" in path]
    # At the root, not under `/api/v1`: the door has a hostname of its own (ADR 0148 §8).
    assert (await client.post(f"/api/v1{POLL}", content=b"{}")).status_code == 404
    refused(await client.post(POLL, content=b"{}"), "invalid_signature", 401)


# ───────────────────────── enrol ─────────────────────────


async def test_an_instance_enrols_through_the_door_and_is_born_pending(
    client: AsyncClient, admin: str, door: kit.Door
) -> None:
    from choregos_api.db.models import OutpostInstance

    await kit.create(client, SLUG)
    daemon = Daemon()
    response = await daemon.enrol(client, await kit.token_for(client, SLUG))
    assert response.status_code == 201, response.text
    answered = response.json()
    daemon_kit.is_the_schema("outpost-enrolment-answer.schema.json", answered)
    outpost.OutpostEnrolmentAnswer.model_validate(answered)
    assert answered | {"instance_id": None, "server_time": None} == {
        "instance_id": None,
        "organisation": kit.ORG,
        "outpost": SLUG,
        "status": "pending",
        # What the daemon prints from its own key and a person compares in the console.
        "fingerprint": outpost.fingerprint_of(daemon.signing_key),
        "protocol": {"current": 1, "minimum": 1},
        "server_time": None,
    }
    assert response.headers[NONCE_HEADER], "the nonce of the first signed request"
    (row,) = await kit.rows_of(SLUG, OutpostInstance)
    assert (row.id, row.status, row.signing_key, row.sealing_key) == (
        answered["instance_id"],
        "pending",
        daemon.signing_key,
        daemon.sealing_key,
    )
    assert (row.daemon_version, row.capabilities, row.sequence) == ("0.27.0", ["tools"], 0)
    assert row.facts == {"hostname": "outpost-0.tools.svc", "platform": "linux/amd64"}
    # Nothing the door sends pins a key: the keys to pin come with the token, to a person.
    assert not [value for value in answered.values() if value in door.keys]
    # Pending, it is handed nothing: its polls say so until a person approves it.
    refused(await daemon.poll(client), "instance_pending", 403)


async def test_an_enrolment_not_signed_with_the_key_it_enrols_is_refused_and_keeps_the_token(
    client: AsyncClient, admin: str
) -> None:
    """The enroller proves it holds the key: a proxy on the path that swapped a key in the body would
    have to forge this signature (ADR 0148 §4)."""
    await kit.create(client, SLUG)
    token = await kit.token_for(client, SLUG)
    daemon, other = Daemon(), Ed25519PrivateKey.generate()
    refused(await daemon.enrol(client, token, signer=other), "invalid_signature", 401)
    # A signature that carries what only a session's does is not an enrolment's.
    refused(await daemon.enrol(client, token, header={"kid": "someone"}), "invalid_signature", 401)
    refused(
        await daemon.send(client, ENROL, daemon.enrolment(token), signature=None), "invalid_signature", 401
    )
    # The sealing key is tied to the signing key by the same signature.
    swapped = daemon.enrolment(token, sealing_key=Daemon().sealing_key)
    signed_for_the_other = daemon.sign(ENROL, compact(daemon.enrolment(token)), kid=None)
    refused(
        await daemon.send(client, ENROL, swapped, signature=signed_for_the_other), "request_mismatch", 401
    )
    assert (await daemon.enrol(client, token)).status_code == 201, "a refused enrolment did not use the token"


async def test_an_enrolment_binds_its_url_and_its_time(client: AsyncClient, admin: str) -> None:
    await kit.create(client, SLUG)
    token = await kit.token_for(client, SLUG)
    daemon = Daemon()
    elsewhere = "https://outposts.example.org/outposts/v1/enrol"
    refused(await daemon.enrol(client, token, url=elsewhere), "request_mismatch", 401)
    refused(await daemon.enrol(client, token, method="PUT"), "request_mismatch", 401)
    stale = refused(await daemon.enrol(client, token, iat=int(time.time()) - 65), "stale_iat", 401)
    assert abs(datetime.fromisoformat(stale["server_time"]) - datetime.now(UTC)) < timedelta(seconds=5)
    refused(await daemon.enrol(client, token, iat=int(time.time()) + 65), "stale_iat", 401)
    assert (await daemon.enrol(client, token)).status_code == 201


async def test_a_body_in_which_no_signing_key_can_be_read_is_a_422_before_the_signature(
    client: AsyncClient,
) -> None:
    """There is no key to verify the signature with: the contract says 422, not 401."""
    daemon = Daemon()
    for body in (b"", b"not json", b"[]", compact({"token": "t" * 20}), compact({"signing_key": "short"})):
        refused(await daemon.send(client, ENROL, body, signature=None), "invalid_request", 422)
        refused(await daemon.send(client, ENROL, body, kid=None), "invalid_request", 422)


async def test_an_enrolment_token_is_checked_where_the_instance_would_be(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the signature and before the protocol and the schema; and a request refused further down
    does not use the token up."""
    from choregos_api.outposts import auth

    await kit.create(client, SLUG)
    daemon = Daemon()
    unknown = "choe_" + "0" * 32 + "x" * 32
    invalid_too = daemon.enrolment(unknown, daemon_version="", unknown_field=True)
    refused(await daemon.send(client, ENROL, invalid_too, kid=None), "invalid_enrolment_token", 401)
    refused(await daemon.enrol(client, "t" * 32), "invalid_enrolment_token", 401)
    no_token = {name: value for name, value in daemon.enrolment("unused").items() if name != "token"}
    refused(await daemon.send(client, ENROL, no_token, kid=None), "invalid_enrolment_token", 401)

    token = await kit.token_for(client, SLUG)
    invalid = daemon.enrolment(token, daemon_version="", unknown_field=True)
    shown = refused(await daemon.send(client, ENROL, invalid, kid=None), "invalid_request", 422)
    assert {tuple(error["loc"]) for error in shown["errors"]} == {("daemon_version",), ("unknown_field",)}
    monkeypatch.setattr(auth, "PROTOCOL_MINIMUM", 2)
    monkeypatch.setattr(auth, "PROTOCOL_CURRENT", 2)
    # Too old for the body's present shape: told so, not that its body is invalid.
    too_old = refused(await daemon.send(client, ENROL, invalid, kid=None), "protocol_unsupported", 426)
    assert too_old["protocol"] == {"current": 2, "minimum": 2}
    monkeypatch.setattr(auth, "PROTOCOL_MINIMUM", 1)
    assert (await daemon.enrol(client, token)).status_code == 201, "the token was still whole"
    refused(await Daemon().enrol(client, token), "invalid_enrolment_token", 401)


async def test_enrolments_under_one_outposts_tokens_are_rate_limited(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An enrolment has no instance to count against yet: it counts against the outpost its token is
    bound to, once that token is known to be good, and the token stays whole."""
    from choregos_api.outposts import auth

    await kit.create(client, SLUG)
    await kit.create(client, "cold-store")
    token, elsewhere = await kit.token_for(client, SLUG), await kit.token_for(client, "cold-store")
    kit.configure(monkeypatch, outposts_door_rate_per_minute="2")
    monkeypatch.setattr(auth, "_limiter", auth.RateLimiter(0))
    daemon = Daemon()
    invalid = daemon.enrolment(token, daemon_version="")
    for _ in range(2):
        refused(await daemon.send(client, ENROL, invalid, kid=None), "invalid_request", 422)
    response = await daemon.enrol(client, token)
    problem = refused(response, "rate_limited", 429)
    assert problem["retry_after"] >= 1 and NONCE_HEADER not in response.headers
    # A token that is not good is refused for that, however many times: it reaches no rate.
    refused(await daemon.enrol(client, "t" * 32), "invalid_enrolment_token", 401)
    assert (await Daemon().enrol(client, elsewhere)).status_code == 201, "another outpost's count"
    kit.configure(monkeypatch, outposts_door_rate_per_minute="0")
    assert (await daemon.enrol(client, token)).status_code == 201, "the token was still whole"


async def test_a_key_that_was_approved_does_not_enrol_again(client: AsyncClient, daemon: Daemon) -> None:
    """A key still pending enrols again as the same instance; one a person approved, or revoked, does
    not (409), and the token stays whole."""
    token = await kit.token_for(client, SLUG)
    refused(await daemon.enrol(client, token), "key_already_enrolled", 409)
    pending = Daemon()
    first = await pending.enrol(client, token)
    again = await pending.enrol(client, await kit.token_for(client, SLUG))
    assert (first.status_code, again.status_code) == (201, 201)
    assert first.json()["instance_id"] == again.json()["instance_id"], "a lost answer keeps its keys"


# ───────────────────────── the signature, check by check ─────────────────────────


async def test_a_signed_poll_is_taken(client: AsyncClient, daemon: Daemon) -> None:
    response = await daemon.poll(client)
    assert response.status_code == 200, response.text
    answered = response.json()
    daemon_kit.is_a("PollAnswer", answered)
    assert answered | {"server_time": None} == {
        "protocol": {"current": 1, "minimum": 1},
        "server_time": None,
        "stop": False,
        "calls": [],
        "cancel": [],
    }
    assert response.headers[NONCE_HEADER]
    row = await daemon_kit.instance_row(str(daemon.instance_id))
    assert row.sequence == daemon.sequence, "the row keeps the last sequence the door took"


async def test_a_request_without_a_signature_is_refused_without_an_alert(
    client: AsyncClient, daemon: Daemon
) -> None:
    """What the daemon's `doctor` sends to see that the door is there: an unsigned `{}` poll. It reads
    a 401 `invalid_signature` problem that carries the door's time; nothing is raised for it."""
    before = await _door_events()
    response = await client.post(POLL, content=b"{}")
    problem = refused(response, "invalid_signature", 401)
    assert abs(datetime.fromisoformat(problem["server_time"]) - datetime.now(UTC)) < timedelta(seconds=5)
    assert response.headers[NONCE_HEADER]
    assert await _door_events() == before


async def test_a_signature_the_door_cannot_verify_is_refused_whatever_is_wrong_with_it(
    client: AsyncClient, daemon: Daemon
) -> None:
    """Absent, malformed, made by a key the door does not know, or not verifying: one answer, which
    does not say which (`invalid_signature`)."""
    body = daemon.poll_body()
    stranger = Ed25519PrivateKey.generate()
    another_outposts = "0" * 32 + str(daemon.instance_id)[32:]
    details = set()
    for bent in (
        {"signature": None},
        {"signature": "not-a-signature"},
        {"signature": "a.b.c"},
        {"signature": "x" * 5000},
        {"signer": stranger},  # the instance's id, signed by someone else
        {"kid": "no-such-instance"},
        {"kid": another_outposts},  # an outpost that does not exist
        {"kid": str(daemon.instance_id)[:32] + "f" * 32},  # this outpost, no such instance
        {"header": {"typ": "JWT"}},
        {"header": {"typ": outpost.CALL_SIGNATURE_TYPE}},  # a call's signature is not a request's
        {"header": {"jwk": {"kty": "OKP"}}},  # nothing but `alg`, `typ` and `kid`
        {"claims": {"iat": "now"}},
        {"iat": 10**400},  # a number no clock ever showed
        {"claims": {"htu": None}},
        {"seq": None},
        {"seq": 0},
        {"seq": True},
        {"seq": 2**63},
        {"seq": "41"},
    ):
        problem = refused(await daemon.send(client, POLL, body, **bent), "invalid_signature", 401)
        details.add(problem["detail"])
    assert len(details) == 1, "the door does not say which"
    tampered = daemon.sign(POLL, compact(body))
    tampered = tampered[:-4] + ("AAAA" if not tampered.endswith("AAAA") else "BBBB")
    refused(await daemon.send(client, POLL, body, signature=tampered), "invalid_signature", 401)
    assert (await daemon.poll(client)).status_code == 200, "none of them took a sequence"


async def test_a_valid_signature_over_another_method_or_url_is_refused(
    client: AsyncClient, daemon: Daemon
) -> None:
    """The signature verifies, and binds something else (`request_mismatch`)."""
    body = daemon.poll_body()
    for bent in (
        {"method": "GET"},
        {"url": f"{kit.ORIGIN}{ENROL}"},  # another verb
        {"url": f"{kit.ORIGIN}{answer_path(UNKNOWN_CALL)}"},
        {"url": f"https://outposts.example.org{POLL}"},  # another door
        {"url": f"http://outposts.example.com{POLL}"},  # another scheme
        {"url": f"{kit.ORIGIN}:8443{POLL}"},
        {"url": f"{kit.ORIGIN}{POLL}?as=someone"},
        {"url": f"{kit.ORIGIN}{POLL}#fragment"},
        {"url": f"https://user@outposts.example.com{POLL}"},
        {"url": POLL},
        {"url": f"{kit.ORIGIN}/api/v1{POLL}"},
    ):
        refused(await daemon.send(client, POLL, body, **bent), "request_mismatch", 401)
    # The same request signed for another call does not pass for this one.
    signed = daemon.sign(refuse_path(UNKNOWN_CALL), compact({"code": "busy"}))
    elsewhere = await daemon.send(client, refuse_path("another-call"), {"code": "busy"}, signature=signed)
    refused(elsewhere, "request_mismatch", 401)
    # The door compares with its own public URL: scheme and host in lower case, a default port left out.
    for written in (f"HTTPS://Outposts.Example.COM{POLL}", f"{kit.ORIGIN}:443{POLL}"):
        assert (await daemon.send(client, POLL, body, url=written)).status_code == 200, written


async def test_a_signature_over_another_body_is_refused(client: AsyncClient, daemon: Daemon) -> None:
    signed = daemon.sign(POLL, compact(daemon.poll_body(capacity=1)))
    sent = await daemon.send(client, POLL, daemon.poll_body(capacity=64), signature=signed)
    refused(sent, "request_mismatch", 401)
    # The digest is of the body exactly as sent: the same document spelt otherwise is another body.
    spelt = json.dumps(daemon.poll_body(capacity=1), indent=1).encode()
    refused(await daemon.send(client, POLL, spelt, signature=signed), "request_mismatch", 401)


async def test_a_stale_iat_is_refused_with_the_doors_time(client: AsyncClient, daemon: Daemon) -> None:
    """60 seconds either way; the refusal carries `server_time`, so a daemon whose clock drifts learns it."""
    from choregos_contracts import IAT_WINDOW_S

    assert IAT_WINDOW_S == 60
    for drift in (-(IAT_WINDOW_S + 5), IAT_WINDOW_S + 5, -1_000_000_000):
        sent = await daemon.send(client, POLL, daemon.poll_body(), iat=int(time.time()) + drift)
        problem = refused(sent, "stale_iat", 401)
        told = datetime.fromisoformat(problem["server_time"])
        assert abs(told - datetime.now(UTC)) < timedelta(seconds=5)
    for drift in (-(IAT_WINDOW_S - 10), IAT_WINDOW_S - 10):
        sent = await daemon.send(client, POLL, daemon.poll_body(), iat=int(time.time()) + drift)
        assert sent.status_code == 200, drift


async def test_a_stale_nonce_is_refused_and_the_refusal_carries_a_fresh_one(
    client: AsyncClient, daemon: Daemon
) -> None:
    """Absent, too old, or not one this door issued. A daemon's first signed request has none: it
    signs again with the one the refusal carries."""
    body = daemon.poll_body()
    forged = f"v1.{int(time.time())}.{'A' * 43}"
    for nonce in (None, _nonce(age_s=301), forged, "v1.1794477600.example-nonce", "", _nonce() + "x", 41):
        response = await daemon.send(client, POLL, body, nonce=nonce)
        refused(response, "stale_nonce", 401)
        fresh = response.headers[NONCE_HEADER]
        assert fresh != nonce
        assert (await daemon.send(client, POLL, body, nonce=fresh)).status_code == 200
    # It proves freshness, it is not single use: what refuses a replay is the sequence.
    kept = _nonce(age_s=290)
    for _ in range(2):
        assert (await daemon.send(client, POLL, body, nonce=kept)).status_code == 200
    # The door stores nothing to check it: another door with the same signing key takes it too.
    from choregos_api.outposts import auth

    assert auth.Nonces(auth._nonce_key(_settings().outposts_door_signing_key)).fresh(kept)
    assert not auth.Nonces(b"k" * 32).fresh(kept)


async def test_a_replayed_sequence_is_refused(client: AsyncClient, daemon: Daemon) -> None:
    """The row keeps the last sequence it took: the very same signed request, sent again, is a replay,
    and so is any sequence that is not above it."""
    body = compact(daemon.poll_body())
    signed = daemon.sign(POLL, body)
    assert (await daemon.send(client, POLL, body, signature=signed)).status_code == 200
    refused(await daemon.send(client, POLL, body, signature=signed), "replayed_sequence", 401)
    taken = daemon.sequence
    for seq in (taken, taken - 1, 1):
        refused(await daemon.send(client, POLL, body, seq=seq), "replayed_sequence", 401)
    # Gaps are allowed; 64 bits, since a daemon numbers its requests from its clock in milliseconds.
    for seq in (taken + 1, taken + 10_000_000, 2**53, 2**63 - 1):
        assert (await daemon.send(client, POLL, body, seq=seq)).status_code == 200, seq
    assert (await daemon_kit.instance_row(str(daemon.instance_id))).sequence == 2**63 - 1
    refused(await daemon.send(client, POLL, body, seq=2**63 - 1), "replayed_sequence", 401)


async def test_a_sequence_is_taken_once_the_earlier_checks_pass_and_whatever_is_refused_next(
    client: AsyncClient, daemon: Daemon
) -> None:
    body = daemon.poll_body()
    sequence = daemon.next_sequence() + 5
    # Refused before the sequence is looked at: it took none, and the daemon may sign it again.
    refused(await daemon.send(client, POLL, body, seq=sequence, nonce=None), "stale_nonce", 401)
    refused(await daemon.send(client, POLL, body, seq=sequence, iat=1), "stale_iat", 401)
    refused(await daemon.send(client, POLL, body, seq=sequence, method="GET"), "request_mismatch", 401)
    assert (await daemon_kit.instance_row(str(daemon.instance_id))).sequence < sequence
    # Refused after it: the sequence is the row's, and the same request is a replay from then on.
    invalid = daemon.poll_body(capacity="many")
    refused(await daemon.send(client, POLL, invalid, seq=sequence), "invalid_request", 422)
    assert (await daemon_kit.instance_row(str(daemon.instance_id))).sequence == sequence
    refused(await daemon.send(client, POLL, body, seq=sequence), "replayed_sequence", 401)


async def test_a_sequence_stays_taken_when_the_request_then_fails(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sequence is committed as soon as it is taken: a fault further down, which rolls the rest of
    the request back, does not give a copy of that request a second chance."""
    from choregos_api.outposts import auth

    def broken(_document: Any) -> None:
        raise RuntimeError("a fault after the signature was taken")

    body = compact(daemon.poll_body())
    signed = daemon.sign(POLL, body)
    with monkeypatch.context() as patched:
        patched.setattr(auth, "speaks", broken)
        assert (await daemon.send(client, POLL, body, signature=signed)).status_code == 500
    assert (await daemon_kit.instance_row(str(daemon.instance_id))).sequence == daemon.sequence
    refused(await daemon.send(client, POLL, body, signature=signed), "replayed_sequence", 401)


async def test_a_pending_instance_is_refused(client: AsyncClient, admin: str) -> None:
    await kit.create(client, SLUG)
    pending = await daemon_kit.enrolled(client)
    refused(await pending.poll(client), "instance_pending", 403)
    refused(await pending.refuse(client, UNKNOWN_CALL), "instance_pending", 403)
    refused(
        await pending.send(client, answer_path(UNKNOWN_CALL), {"sealed": "A" * 80}), "instance_pending", 403
    )
    await kit.approve(client, SLUG, str(pending.instance_id))
    assert (await pending.poll(client)).status_code == 200


async def test_a_revoked_instance_is_refused(client: AsyncClient, daemon: Daemon) -> None:
    """Revocation holds from the next request: the door reads the row every time (ADR 0147 §5)."""
    assert (await daemon.poll(client)).status_code == 200
    revoked = await client.post(f"{kit.BASE}/{SLUG}/instances/{daemon.instance_id}/revoke")
    assert revoked.status_code == 200, revoked.text
    response = await daemon.poll(client)
    refused(response, "instance_revoked", 403)
    assert response.headers[NONCE_HEADER]
    refused(await daemon.refuse(client, UNKNOWN_CALL), "instance_revoked", 403)


async def test_the_instances_of_a_revoked_outpost_are_refused(client: AsyncClient, daemon: Daemon) -> None:
    from choregos_api.db.models import Outpost, OutpostInstance

    assert (await client.post(f"{kit.BASE}/{SLUG}/revoke")).status_code == 200
    refused(await daemon.poll(client), "instance_revoked", 403)
    # The outpost's own status is read, not only what the revocation wrote on its instances.
    await daemon_kit.change(OutpostInstance, str(daemon.instance_id), status="active")
    refused(await daemon.refuse(client, UNKNOWN_CALL), "instance_revoked", 403)
    refused(await daemon.poll(client), "instance_revoked", 403)
    await daemon_kit.change(Outpost, (await kit.outpost_row(SLUG)).id, status="active")
    refused(await daemon.refuse(client, UNKNOWN_CALL), "not_found", 404)


async def test_a_request_that_fails_several_checks_is_answered_for_the_first(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract's order: signature, binding, `iat`, nonce, sequence, instance, rate, protocol,
    schema, then the verb."""
    from choregos_api.outposts import auth

    await kit.create(client, SLUG)
    pending = await daemon_kit.enrolled(client)
    body = pending.poll_body(protocol=0, unknown_field=True)
    stale, other = int(time.time()) - 600, Ed25519PrivateKey.generate()
    sent = [
        ({"signer": other, "method": "GET", "iat": stale, "nonce": None}, "invalid_signature"),
        ({"method": "GET", "iat": stale, "nonce": None}, "request_mismatch"),
        ({"iat": stale, "nonce": None}, "stale_iat"),
        ({"nonce": None}, "stale_nonce"),
    ]
    for bent, code in sent:
        refused(await pending.send(client, POLL, body, **bent), code, 401)
    assert (await pending.send(client, POLL, body)).status_code == 403  # takes its sequence
    refused(await pending.send(client, POLL, body, seq=pending.sequence), "replayed_sequence", 401)
    refused(await pending.send(client, POLL, body), "instance_pending", 403)

    await kit.approve(client, SLUG, str(pending.instance_id))
    kit.configure(monkeypatch, outposts_door_rate_per_minute="1")
    monkeypatch.setattr(auth, "_limiter", auth.RateLimiter(0))
    refused(await pending.send(client, POLL, body), "protocol_unsupported", 426)
    refused(await pending.send(client, POLL, body), "rate_limited", 429)
    kit.configure(monkeypatch, outposts_door_rate_per_minute="0")
    refused(await pending.send(client, POLL, body), "protocol_unsupported", 426)
    refused(await pending.send(client, POLL, pending.poll_body(unknown_field=True)), "invalid_request", 422)
    # A verb's own refusal comes last: an invalid body for a call that does not exist is a 422.
    refused(
        await pending.send(client, refuse_path(UNKNOWN_CALL), {"code": "sulking"}), "invalid_request", 422
    )
    refused(await pending.refuse(client, UNKNOWN_CALL), "not_found", 404)


# ───────────────────────── bounds a hostile outpost meets ─────────────────────────


def _paths() -> dict[str, str]:
    return {
        "enrol": ENROL,
        "poll": POLL,
        "answer": answer_path(UNKNOWN_CALL),
        "refuse": refuse_path(UNKNOWN_CALL),
    }


@pytest.mark.parametrize("verb", ["enrol", "poll", "answer", "refuse"])
async def test_a_body_above_the_cap_is_refused_before_anything_is_parsed(
    client: AsyncClient, verb: str
) -> None:
    """The size is the first check: a body one byte above its verb's cap is a 413 whatever it holds
    and whoever sent it, before the signature is looked at (ADR 0148 §9)."""
    cap, path = MAX_BODY_BYTES[verb], _paths()[verb]
    assert cap == daemon_kit.contracts.load_outpost_door()["x-bounds"]["max_body_bytes"][verb]
    response = await client.post(path, content=b"x" * (cap + 1))
    refused(response, "body_too_large", 413)
    assert NONCE_HEADER not in response.headers
    # With a signature too, valid or not: the size is judged first.
    signed = await client.post(path, content=b"x" * (cap + 1), headers={SIGNATURE_HEADER: "a.b.c"})
    refused(signed, "body_too_large", 413)
    # At the cap the body is read, and refused for what it is: not JSON, or not signed.
    at_the_cap = await client.post(path, content=b"x" * cap)
    assert at_the_cap.status_code == (422 if verb == "enrol" else 401), at_the_cap.text


async def _raw_post(
    app: Any, path: str, chunks: list[Any], headers: dict[str, str], gone: asyncio.Event | None = None
) -> tuple[int, dict[str, str], bytes]:
    """A request handed to the application as a server hands it: the body in the chunks given, the
    headers as written (a `content-length` that lies stays as it is), then the client going away when
    `gone` is set."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
        "client": ("203.0.113.7", 40000),
        "server": ("test", 80),
    }
    pending, sent = list(chunks), []

    async def receive() -> dict[str, Any]:
        if pending and pending[0] is None:
            return {"type": "http.disconnect"}  # the client went away with its body half sent
        if pending:
            return {"type": "http.request", "body": pending.pop(0), "more_body": bool(pending)}
        if gone is not None:
            await gone.wait()
        else:
            await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, body


async def test_the_cap_counts_the_bytes_received_not_a_header(app: Any) -> None:
    """A `Content-Length` that says ten bytes does not let a megabyte through, and a body sent in
    chunks with no length at all is counted as it comes."""
    cap = MAX_BODY_BYTES["refuse"]
    path = refuse_path(UNKNOWN_CALL)
    lying = await _raw_post(app, path, [b"x" * (cap + 1)], {"content-length": "10"})
    chunked = await _raw_post(app, path, [b"x" * 1000] * 5, {"transfer-encoding": "chunked"})
    for status, _headers, body in (lying, chunked):
        assert status == 413, body
        assert json.loads(body)["code"] == "body_too_large"
    honest = await _raw_post(app, path, [b"x" * 1000] * 4, {"transfer-encoding": "chunked"})
    assert honest[0] == 401, "under the cap it is read, and refused for its signature"


async def test_a_body_that_does_not_arrive_whole_is_refused_and_is_no_fault_of_the_door(app: Any) -> None:
    """A client that goes away with its body half sent is answered, to nobody, as a body that is not
    the verb's: it is not an error of the API, and does not count as one."""
    status, _headers, body = await _raw_post(app, POLL, [b'{"protocol":', None], {})
    assert status == 422, body
    assert json.loads(body)["code"] == "invalid_request"


async def test_an_instance_that_sends_too_much_is_told_when_to_come_back(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rate limit per instance, in memory and per replica like every limit of the API: a 429 with
    `retry_after`. It is one instance's: another's requests are not counted with them."""
    from choregos_api.outposts import auth

    kit.configure(monkeypatch, outposts_door_rate_per_minute="3")
    monkeypatch.setattr(auth, "_limiter", auth.RateLimiter(0))
    other = await daemon_kit.approved(client, create=False)
    for _ in range(3):
        assert (await daemon.poll(client)).status_code == 200
    response = await daemon.poll(client)
    problem = refused(response, "rate_limited", 429)
    assert 1 <= problem["retry_after"] <= 61 and response.headers["retry-after"] == str(
        problem["retry_after"]
    )
    assert NONCE_HEADER not in response.headers
    refused(await daemon.refuse(client, UNKNOWN_CALL), "rate_limited", 429)
    assert (await other.poll(client)).status_code == 200
    # The request was signed and its sequence taken before the rate was looked at.
    assert (await daemon_kit.instance_row(str(daemon.instance_id))).sequence == daemon.sequence


async def test_a_daemon_below_the_minimum_version_is_refused_with_the_minimum(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean refusal that names the window, and no assignment (ADR 0148 §3): the daemon stops
    polling and says so; nobody upgrades it for its operator."""
    from choregos_api.outposts import auth

    placed = await daemon_kit.place()
    monkeypatch.setattr(auth, "PROTOCOL_MINIMUM", 2)
    monkeypatch.setattr(auth, "PROTOCOL_CURRENT", 3)
    response = await daemon.poll(client, protocol=1)
    problem = refused(response, "protocol_unsupported", 426)
    assert problem["protocol"] == {"current": 3, "minimum": 2}
    assert "protocol 2" in problem["detail"] and "speaks 1" in problem["detail"]
    assert response.headers["upgrade"] == "choregos-outpost/2"
    assert NONCE_HEADER not in response.headers
    assert (await daemon_kit.call_row(placed.id)).state == "pending", "it was handed nothing"
    # Read from the body before the rest of it is judged: a daemon too old for the body's present
    # shape is told so, not that its body is invalid.
    odd = daemon.poll_body(protocol=1, capacity="many", a_field_of_protocol_0=True)
    refused(await daemon.send(client, POLL, odd), "protocol_unsupported", 426)
    # A daemon inside the window is served, and reads the window in the answer.
    served = await daemon.poll(client, protocol=2)
    assert served.status_code == 200 and served.json()["protocol"] == {"current": 3, "minimum": 2}
    assert [call["id"] for call in served.json()["calls"]] == [placed.id]


@pytest.mark.parametrize(
    ("body", "where"),
    [
        (b"", ()),
        (b"not json", ()),
        (b"[1, 2]", ()),
        (b'{"protocol":1}', ("daemon_version",)),
        ({"unknown_field": 1}, ("unknown_field",)),
        ({"capacity": 65}, ("capacity",)),
        ({"capacity": "4"}, ("capacity",)),
        ({"wait_s": 26}, ("wait_s",)),
        ({"capabilities": ["runs"]}, ("capabilities", "0")),
        ({"endpoints": ["Tracker"]}, ("endpoints", "0")),
        ({"endpoints": ["http://tracker.internal"]}, ("endpoints", "0")),
        ({"running": ["a/b"]}, ("running", "0")),
    ],
)
async def test_a_poll_that_is_not_what_the_schema_describes_is_a_422_of_the_door(
    client: AsyncClient, daemon: Daemon, body: Any, where: tuple[str, ...]
) -> None:
    """Requests are closed shapes, parsed by hand: the problem is the door's, with its `code` and its
    `server_time`, and says where the body departs from the schema."""
    sent = body if isinstance(body, bytes) else daemon.poll_body(**body)
    response = await daemon.send(client, POLL, sent)
    problem = refused(response, "invalid_request", 422)
    assert NONCE_HEADER not in response.headers
    if where:
        assert where in {tuple(error["loc"]) for error in problem["errors"]}, problem


# ───────────────────────── poll: what is handed over ─────────────────────────


async def test_a_call_is_handed_over_sealed_to_the_instance_and_signed_by_the_door(
    client: AsyncClient, daemon: Daemon, door: kit.Door
) -> None:
    from choregos_api.services import outposts as service

    asserted = {
        "sub": "run:r-42",
        "org": kit.ORG,
        "run": "r-42",
        "agent": "triage",
        "approved_by": ["ada@example.org"],
    }
    arguments = {"title": "Door stuck — Δ of 3 €", "labels": ["bug"]}
    placed = await daemon_kit.place(
        tool="create_issue", arguments=arguments, write=True, asserted=asserted, timeout_s=120
    )
    waiting = await daemon_kit.call_row(placed.id)
    assert (waiting.state, waiting.instance_id, waiting.epoch, waiting.deadline) == (
        "pending",
        None,
        None,
        None,
    )
    assert service.open_call_arguments(waiting) == arguments, "sealed at rest while they wait"
    assert arguments["title"].encode() not in bytes(waiting.arguments_ciphertext)

    before = datetime.now(UTC)
    response = await daemon.poll(client, daemon_version="0.27.1", endpoints=["tracker", "forge"])
    assert response.status_code == 200, response.text
    answered = response.json()
    daemon_kit.is_a("PollAnswer", answered)
    (handed,) = answered["calls"]
    assert set(handed) == {"id", "sealed", "signature"} and handed["id"] == placed.id
    # Signed by the key in service, the first of the keys an operator pins; no other key verifies it.
    assert daemon.verifies(handed, [door.signing_public])
    assert not daemon.verifies(handed, [door.announced])
    assert not Daemon(instance_id="another-instance").verifies(handed, door.keys), "it names its instance"
    call = daemon.opened(handed)
    deadline = datetime.fromisoformat(call.pop("deadline"))
    answer_key = call.pop("answer_key")
    assert call == {
        "id": placed.id,
        "kind": "call_tool",
        "endpoint": "tracker",
        "tool": "create_issue",
        "arguments": arguments,
        "asserted": asserted,
    }
    # The call says nothing of whether the tool writes: the outpost reads that from its own server.
    assert "write" not in call
    assert timedelta(seconds=110) < deadline - before <= timedelta(seconds=121)
    # Only this instance opens it.
    with pytest.raises(daemon_kit.sealing.SealError):
        daemon_kit.sealing.open_sealed(Daemon().sealing, handed["id"], handed["sealed"])
    with pytest.raises(daemon_kit.sealing.SealError):
        daemon_kit.sealing.open_sealed(daemon.sealing, "another-call", handed["sealed"])

    claimed = await daemon_kit.call_row(placed.id)
    assert (claimed.state, claimed.instance_id, claimed.epoch) == ("claimed", daemon.instance_id, 0)
    assert abs(claimed.deadline.replace(tzinfo=UTC) - deadline) < timedelta(seconds=1)
    assert claimed.answer_key == answer_key and claimed.answer_key_ciphertext is not None
    assert claimed.arguments_sha256 == outpost.arguments_sha256(arguments)
    # What the poll says of the instance is written, and it was seen.
    row = await daemon_kit.instance_row(str(daemon.instance_id))
    assert (row.daemon_version, row.endpoints, row.capabilities) == (
        "0.27.1",
        ["tracker", "forge"],
        ["tools"],
    )
    assert abs(row.last_seen_at.replace(tzinfo=UTC) - before) < timedelta(seconds=5)
    assert row.poll_holder is None, "the slot is given back when the poll answers"
    # Claimed, it is this instance's from that answer on: no later poll hands it over again.
    assert await daemon.calls(client, endpoints=["tracker", "forge"]) == []
    shown = (await client.get(f"{kit.BASE}/{SLUG}")).json()
    assert shown["online"] is True and shown["endpoints"] == ["forge", "tracker"]


async def test_a_lease_lasts_300_seconds_at_most_and_nothing_a_daemon_sends_extends_it(
    client: AsyncClient, daemon: Daemon
) -> None:
    placed = await daemon_kit.place(timeout_s=300)
    before = datetime.now(UTC)
    (call,) = await daemon.calls(client, wait_s=25)
    lease = datetime.fromisoformat(call["deadline"]) - before
    assert timedelta(seconds=290) < lease <= timedelta(seconds=301)
    row = await daemon_kit.call_row(placed.id)
    # Holding it, and saying so at every poll, moves nothing.
    await daemon.calls(client, running=[placed.id])
    assert (await daemon_kit.call_row(placed.id)).deadline == row.deadline
    with pytest.raises(ValueError, match="300 seconds at most"):
        await daemon_kit.place(timeout_s=301)
    # The bound is the lease's own: a row that says it waits an hour is still leased for 300 seconds.
    from choregos_api.db.models import OutpostCall

    patient = await daemon_kit.place(endpoint="forge")
    await daemon_kit.change(OutpostCall, patient.id, expires_at=datetime.now(UTC) + timedelta(hours=1))
    (call,) = await daemon.calls(client, endpoints=["forge"])
    assert datetime.fromisoformat(call["deadline"]) - datetime.now(UTC) <= timedelta(seconds=301)


async def test_a_poll_hands_over_no_more_than_its_capacity_and_only_what_it_announced(
    client: AsyncClient, daemon: Daemon
) -> None:
    tracker = [(await daemon_kit.place()).id for _ in range(3)]
    forge = (await daemon_kit.place(endpoint="forge")).id
    assert await daemon.calls(client, capacity=0) == []
    assert await daemon.calls(client, endpoints=[]) == []
    assert await daemon.calls(client, endpoints=["wiki"]) == []
    # An instance is used only for what it announces: without `tools` it is handed no tool call.
    assert await daemon.calls(client, capabilities=[]) == []
    first = await daemon.calls(client, capacity=2)
    assert [call["id"] for call in first] == tracker[:2], "the oldest first, no more than the capacity"
    rest = await daemon.calls(client, capacity=64, endpoints=["tracker", "forge"])
    assert sorted(call["id"] for call in rest) == sorted([tracker[2], forge])


async def test_a_tool_listing_is_a_call_like_another(client: AsyncClient, daemon: Daemon) -> None:
    placed = await daemon_kit.place(kind="list_tools")
    (call,) = await daemon.calls(client)
    assert (call["kind"], call["tool"], call["arguments"]) == ("list_tools", None, None)
    tools = [{"name": "search_issues", "read_only": True}, {"name": "create_issue"}]
    assert (await daemon.answer(client, call, tools=tools)).json() == {"id": placed.id, "recorded": "answer"}


async def test_a_paused_outpost_is_handed_nothing_and_reads_stop(client: AsyncClient, daemon: Daemon) -> None:
    """The switch per outpost (ADR 0148 §10): nothing more is handed to it, and the poll's stop flag
    tells its instances; what they hold may finish."""
    held = await daemon_kit.place()
    (call,) = await daemon.calls(client)
    assert (await client.post(f"{kit.BASE}/{SLUG}/pause")).status_code == 200
    with pytest.raises(Exception, match="paused") as refusal:
        await daemon_kit.place()
    assert getattr(refusal.value, "reason", None) == "paused", "nothing is written for a paused outpost"
    # A call that was waiting when the outpost was paused stays where it is.
    waiting = await _placed_by_hand()
    response = await daemon.poll(client, running=[held.id])
    assert response.status_code == 200, response.text
    assert response.json() | {"server_time": None, "protocol": None} == {
        "protocol": None,
        "server_time": None,
        "stop": True,
        "calls": [],
        "cancel": [],
    }
    assert (await daemon.answer(client, call)).status_code == 200, "what it holds may finish"
    assert (await client.post(f"{kit.BASE}/{SLUG}/resume")).status_code == 200
    resumed = await daemon.poll(client)
    assert resumed.json()["stop"] is False and [c["id"] for c in resumed.json()["calls"]] == [waiting]


async def _placed_by_hand(slug: str = SLUG) -> str:
    """A call that waits at an outpost whatever its status: written as `place` writes one, by hand."""
    from choregos_api.db.models import OutpostCall
    from choregos_api.db.session import name_the_outpost, session_scope
    from choregos_api.outposts import rendezvous
    from choregos_api.services import outposts as service

    row = await kit.outpost_row(slug)
    async with session_scope(orgs="*") as session:
        await name_the_outpost(session, row.id)
        call = OutpostCall(
            id=rendezvous.new_call_id(),
            org_id=row.org_id,
            outpost_id=row.id,
            kind="call_tool",
            endpoint="tracker",
            tool="search_issues",
            write=False,
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
        service.seal_call_arguments(call, {"query": "open"})
        rendezvous._new_answer_key(call)
        session.add(call)
        await session.flush()
        return str(call.id)


async def test_a_call_that_weighs_more_than_the_door_carries_is_not_handed_over(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound is on the call as carried: its id, its envelope and its signature together. A call
    above it is refused where it is admitted; one that reaches a claim all the same is not handed
    over, and is refused there with the code an outpost gives a payload above its ceiling."""
    from choregos_api.outposts import rendezvous

    assert rendezvous.MAX_SEALED_CALL_BYTES == 2 * 1024 * 1024
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place(arguments={"blob": "x" * (2 * 1024 * 1024)})
    assert refusal.value.reason == rendezvous.TOO_LARGE
    fits = await daemon_kit.place(arguments={"blob": "x" * 2000})
    heavy = await daemon_kit.place(arguments={"blob": "y" * 6000})
    monkeypatch.setattr(rendezvous, "MAX_SEALED_CALL_BYTES", 6000)
    response = await daemon.poll(client)
    handed = response.json()["calls"]
    assert [call["id"] for call in handed] == [fits.id]
    assert all(rendezvous.carried_bytes(call) <= 6000 for call in handed)
    row = await daemon_kit.call_row(heavy.id)
    assert (row.state, row.refusal, row.recorded, row.instance_id) == (
        "refused",
        "payload_too_large",
        None,
        None,
    )
    assert row.arguments_ciphertext is None and row.finished_at is not None
    outcome = await rendezvous.wait(row.outpost_id, heavy.id)
    assert outcome is not None and (outcome.state.value, outcome.refusal) == ("refused", "payload_too_large")


async def test_a_door_that_cannot_open_a_calls_arguments_does_not_hand_it_over(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The arguments wait under the deployment's key wrapper. A door's process that does not hold the
    key they were sealed under hands that call to nobody, and still serves the poll: the call runs out
    of time where it is, and what the door can open is handed over."""
    sealed_elsewhere = await daemon_kit.place()
    daemon_kit.key_wrapper(monkeypatch, label="choregos tests: another deployment's key wrapper")
    listing = await daemon_kit.place(kind="list_tools")  # no arguments to open
    response = await daemon.poll(client)
    assert response.status_code == 200, response.text
    assert [call["id"] for call in response.json()["calls"]] == [listing.id]
    assert (await daemon_kit.call_row(sealed_elsewhere.id)).state == "pending"
    daemon_kit.key_wrapper(monkeypatch)
    assert [call["id"] for call in await daemon.calls(client)] == [sealed_elsewhere.id]


async def test_a_poll_answers_what_it_holds_that_is_awaited_no_more(
    client: AsyncClient, daemon: Daemon
) -> None:
    """`cancel`: the calls among `running` an answer to which would not be taken."""
    from choregos_api.db.session import session_scope
    from choregos_api.outposts import rendezvous

    held, done, dropped, late = [(await daemon_kit.place(write=index == 3)).id for index in range(4)]
    calls = {call["id"]: call for call in await daemon.calls(client)}
    assert set(calls) == {held, done, dropped, late}
    assert (await daemon.answer(client, calls[done])).status_code == 200
    outpost_id = (await kit.outpost_row(SLUG)).id
    async with session_scope(orgs=[kit.ORG]) as session:
        assert await rendezvous.cancel(session, outpost_id, dropped) is rendezvous.OutpostCallState.TIMED_OUT
        # A cancel never interrupts a write in flight: nobody knows whether it was executed.
        assert await rendezvous.cancel(session, outpost_id, late) is rendezvous.OutpostCallState.IN_DOUBT
    running = [held, done, dropped, late, UNKNOWN_CALL, held]
    response = await daemon.poll(client, running=running, wait_s=25)
    assert response.json()["cancel"] == [dropped, late, UNKNOWN_CALL]
    # An answer to one of them is what the door said it would be.
    refused(await daemon.answer(client, calls[dropped]), "lease_lost", 409)
    refused(await daemon.answer(client, calls[late]), "lease_lost", 409)
    assert (await daemon_kit.call_row(late)).state == "in_doubt"
    assert (await daemon.answer(client, calls[held])).status_code == 200


# ───────────────────────── poll: one at a time, and it waits ─────────────────────────


async def test_a_poll_with_nothing_to_hand_over_answers_at_the_end_of_its_wait(
    client: AsyncClient, daemon: Daemon, quick_looks: None
) -> None:
    from choregos_api.outposts import door as door_module

    started = time.monotonic()
    assert await daemon.calls(client, wait_s=2) == []
    waited = time.monotonic() - started
    assert door_module.JITTER <= 0.1
    assert 2 * 0.9 - 0.05 <= waited < 6, "the wait asked for, a tenth sooner at most (jitter)"
    started = time.monotonic()
    assert await daemon.calls(client, wait_s=0) == []
    assert time.monotonic() - started < 1.5, "0 answers at once"


async def test_a_waiting_poll_answers_as_soon_as_a_call_is_placed(
    client: AsyncClient, daemon: Daemon, quick_looks: None
) -> None:
    waiting = asyncio.create_task(daemon.calls(client, wait_s=20))
    await _poll_waits(daemon)
    await asyncio.sleep(0.2)
    assert not waiting.done()
    started = time.monotonic()
    placed = await daemon_kit.place()
    (call,) = await asyncio.wait_for(waiting, timeout=5)
    assert call["id"] == placed.id and time.monotonic() - started < 5


async def test_a_second_concurrent_poll_is_a_conflict_and_raises_an_alert(
    client: AsyncClient, daemon: Daemon, quick_looks: None
) -> None:
    """One poll per instance at a time: two holders of one key is what a stolen key looks like."""
    from choregos_api.metrics import refresh
    from choregos_api.services.outposts import POLL_CONFLICT

    first = asyncio.create_task(daemon.poll(client, wait_s=20))
    await _poll_waits(daemon)
    assert await kit.audit_rows(POLL_CONFLICT) == []

    second = await daemon.poll(client, wait_s=20)
    problem = refused(second, "poll_conflict", 409)
    assert 1 <= problem["retry_after"] <= 26 and second.headers["retry-after"] == str(problem["retry_after"])
    assert second.headers[NONCE_HEADER]
    (alert,) = await kit.audit_rows(POLL_CONFLICT)
    row = await kit.outpost_row(SLUG)
    assert (alert.actor_kind, alert.org_id) == ("outpost", row.org_id)
    assert (alert.target_type, alert.target_id) == ("outpost_instance", daemon.instance_id)
    assert alert.payload == {"outpost": SLUG, "fingerprint": outpost.fingerprint_of(daemon.signing_key)}
    assert alert.actor_id == f"outpost:{row.id}:{daemon.instance_id}"
    # What an alert rule reads: the series is computed from those events, the same at every replica.
    await refresh()
    metrics = (await client.get("/metrics")).text
    assert f'choregos_outpost_poll_conflicts_total{{org="{kit.ORG}"}} 1.0' in metrics

    # However many conflicts follow, one alert a minute for an instance.
    for _ in range(3):
        refused(await daemon.poll(client, wait_s=0), "poll_conflict", 409)
    assert len(await kit.audit_rows(POLL_CONFLICT)) == 1
    # The poll that waits is not disturbed: it is handed the next call, and then the slot is free.
    placed = await daemon_kit.place()
    answered = await asyncio.wait_for(first, timeout=5)
    assert [call["id"] for call in answered.json()["calls"]] == [placed.id]
    assert (await daemon.poll(client)).status_code == 200
    # A minute later a conflict is an alert again.
    from choregos_api.db.models import OutpostInstance

    long_ago = datetime.now(UTC) - timedelta(seconds=61)
    await daemon_kit.change(OutpostInstance, str(daemon.instance_id), poll_conflict_at=long_ago)
    again = asyncio.create_task(daemon.poll(client, wait_s=20))
    await _poll_waits(daemon)
    refused(await daemon.poll(client), "poll_conflict", 409)
    assert len(await kit.audit_rows(POLL_CONFLICT)) == 2
    await _cut_short(again, daemon)


async def test_the_slot_is_given_back_when_the_client_goes_away(
    app: Any, client: AsyncClient, daemon: Daemon, quick_looks: None
) -> None:
    """A daemon that restarts must not meet 25 seconds of conflict from its own dead poll."""
    body = compact(daemon.poll_body(wait_s=25))
    headers = {SIGNATURE_HEADER: daemon.sign(POLL, body), "content-type": "application/json"}
    gone = asyncio.Event()
    waiting = asyncio.create_task(_raw_post(app, POLL, [body], headers, gone))
    await _poll_waits(daemon)
    refused(await daemon.poll(client), "poll_conflict", 409)
    started = time.monotonic()
    gone.set()
    await asyncio.wait_for(waiting, timeout=5)
    assert time.monotonic() - started < 5, "the poll ends when its client is gone, not 25 seconds later"
    assert (await daemon_kit.instance_row(str(daemon.instance_id))).poll_holder is None
    assert (await daemon.poll(client)).status_code == 200


async def test_the_slot_is_given_back_when_the_request_is_cut_short(
    client: AsyncClient, daemon: Daemon, quick_looks: None
) -> None:
    waiting = asyncio.create_task(daemon.poll(client, wait_s=25))
    await _poll_waits(daemon)
    await _cut_short(waiting, daemon)
    assert (await daemon.poll(client)).status_code == 200


async def test_a_slot_nobody_gave_back_lapses_by_itself(client: AsyncClient, daemon: Daemon) -> None:
    """A replica that dies gives nothing back: the slot is on the row with an expiry, and the
    instance's next poll, at whichever replica, takes it once it has lapsed."""
    from choregos_api.db.models import OutpostInstance

    now = datetime.now(UTC)
    held = {"poll_holder": "a-replica-that-died", "poll_expires_at": now + timedelta(seconds=12)}
    await daemon_kit.change(OutpostInstance, str(daemon.instance_id), **held)
    problem = refused(await daemon.poll(client), "poll_conflict", 409)
    assert 10 <= problem["retry_after"] <= 13, "when the slot lapses"
    lapsed = {"poll_expires_at": now - timedelta(seconds=1)}
    await daemon_kit.change(OutpostInstance, str(daemon.instance_id), **lapsed)
    assert (await daemon.poll(client)).status_code == 200
    assert (await daemon_kit.instance_row(str(daemon.instance_id))).poll_holder is None


async def test_a_poll_that_overstayed_its_slot_hands_nothing_over(
    client: AsyncClient, daemon: Daemon, quick_looks: None
) -> None:
    """A slot lapses, and another poll takes it: the poll that held it finds the row is no longer its
    own at its next look, and ends with nothing rather than claim beside the other."""
    from choregos_api.db.models import OutpostInstance

    waiting = asyncio.create_task(daemon.poll(client, wait_s=20))
    await _poll_waits(daemon)
    await daemon_kit.change(OutpostInstance, str(daemon.instance_id), poll_holder="another-poll")
    response = await asyncio.wait_for(waiting, timeout=5)
    placed = await daemon_kit.place()
    assert response.status_code == 200 and response.json()["calls"] == []
    row = await daemon_kit.instance_row(str(daemon.instance_id))
    assert row.poll_holder == "another-poll", "and it does not empty a slot that is not its own"
    assert (await daemon_kit.call_row(placed.id)).state == "pending"


async def test_a_poll_closes_what_ran_out_of_time_at_its_outpost(client: AsyncClient, daemon: Daemon) -> None:
    """Whoever looks at an outpost's calls closes those whose time is up: an outpost that polls keeps
    its own rows true, with nothing else running."""
    from choregos_api.db.models import OutpostCall

    unclaimed, write = await daemon_kit.place(endpoint="forge"), await daemon_kit.place(write=True)
    assert len(await daemon.calls(client)) == 1
    long_ago = datetime.now(UTC) - timedelta(seconds=1)
    await daemon_kit.change(OutpostCall, unclaimed.id, expires_at=long_ago)
    await daemon_kit.change(OutpostCall, write.id, deadline=long_ago)
    assert await daemon.calls(client, endpoints=["forge"]) == [], "a call whose time is up is not handed over"
    assert (await daemon_kit.call_row(unclaimed.id)).state == "expired"
    assert (await daemon_kit.call_row(write.id)).state == "in_doubt"


async def test_a_poll_whose_instance_is_revoked_while_it_waits_ends_refused(
    client: AsyncClient, daemon: Daemon, quick_looks: None
) -> None:
    waiting = asyncio.create_task(daemon.poll(client, wait_s=20))
    await _poll_waits(daemon)
    assert (await client.post(f"{kit.BASE}/{SLUG}/instances/{daemon.instance_id}/revoke")).status_code == 200
    refused(await asyncio.wait_for(waiting, timeout=5), "instance_revoked", 403)


# ───────────────────────── answer, refuse ─────────────────────────


async def test_an_answer_is_recorded_once_and_stored_sealed_for_whoever_placed_the_call(
    client: AsyncClient, daemon: Daemon
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.outposts import rendezvous

    arguments = {"title": "Door stuck"}
    placed = await daemon_kit.place(tool="create_issue", arguments=arguments, write=True)
    (call,) = await daemon.calls(client)
    result = {"content": [{"type": "text", "text": "Created FAC-12"}], "structuredContent": {"key": "FAC-12"}}
    sealed = daemon.sealed_answer(call, result=result | {"isError": False}, write=True, duration_ms=412)
    response = await daemon.send(client, answer_path(placed.id), {"sealed": sealed})
    assert response.status_code == 200, response.text
    daemon_kit.is_a("CallAck", response.json())
    assert response.json() == {"id": placed.id, "recorded": "answer"} and response.headers[NONCE_HEADER]

    row = await daemon_kit.call_row(placed.id)
    assert (row.state, row.recorded, row.recorded_by, row.recorded_epoch) == (
        "answered",
        "answer",
        daemon.instance_id,
        0,
    )
    assert row.recorded_at is not None and row.finished_at is not None
    # The door stored the envelope as it came and did not open it: it knows nothing of the outcome.
    assert row.answer_sealed == sealed and (row.outcome, row.result_sha256, row.is_error) == (
        None,
        None,
        None,
    )
    # The arguments go when the call completes; their digest stays (ADR 0148 §11).
    assert row.arguments_ciphertext is None and row.asserted is None
    assert row.arguments_sha256 == outpost.arguments_sha256(arguments)

    # Whoever placed the call opens it, with the private half of the key made for this call.
    outcome = await rendezvous.wait(row.outpost_id, placed.id)
    assert outcome is not None and outcome.state is rendezvous.OutpostCallState.ANSWERED
    assert outcome.answer is not None and outcome.answer.result == result | {"isError": False}
    assert (outcome.answer.write, outcome.answer.duration_ms, outcome.instance_id) == (
        True,
        412,
        daemon.instance_id,
    )
    read = await daemon_kit.call_row(placed.id)
    assert (read.outcome, read.is_error) == ("result", False)
    assert read.result_sha256 == outpost.arguments_sha256(result | {"isError": False})
    # Read, it is forgotten: what is left of the row is references and digests.
    async with session_scope(orgs=[kit.ORG]) as session:
        await rendezvous.forget(session, row.outpost_id, placed.id)
    emptied = await daemon_kit.call_row(placed.id)
    assert (emptied.answer_sealed, emptied.answer_key_ciphertext) == (None, None)
    assert (emptied.state, emptied.recorded, emptied.result_sha256) == (
        "answered",
        "answer",
        read.result_sha256,
    )


async def test_the_same_instance_sending_again_reads_what_was_recorded_whatever_it_sends(
    client: AsyncClient, daemon: Daemon
) -> None:
    """A daemon sends its answer again until the deadline, and after a restart."""
    from choregos_api.outposts import rendezvous

    placed = await daemon_kit.place()
    (call,) = await daemon.calls(client)
    first = daemon.sealed_answer(call)
    assert (await daemon.send(client, answer_path(placed.id), {"sealed": first})).status_code == 200
    for _ in range(2):
        again = await daemon.answer(client, call, outcome="unanswered")
        assert (again.status_code, again.json()) == (200, {"id": placed.id, "recorded": "answer"})
    # The first of an answer and a refusal wins.
    late = await daemon.refuse(client, placed.id, "busy")
    assert (late.status_code, late.json()) == (200, {"id": placed.id, "recorded": "answer"})
    row = await daemon_kit.call_row(placed.id)
    assert (row.answer_sealed, row.refusal, row.state) == (first, None, "answered")
    # Whatever the row's state has become since: a reader moved it, the record did not move.
    outcome = await rendezvous.wait(row.outpost_id, placed.id)
    assert outcome is not None and outcome.answer is not None
    await daemon_kit.change(type(row), placed.id, state="timed_out")
    assert (await daemon.answer(client, call)).json() == {"id": placed.id, "recorded": "answer"}
    # Even after the deadline: it reads what was recorded in time.
    await daemon_kit.change(type(row), placed.id, deadline=datetime.now(UTC) - timedelta(seconds=5))
    assert (await daemon.answer(client, call)).json() == {"id": placed.id, "recorded": "answer"}
    # And were the row leased again by a hand or a fault, what was recorded would still not be written
    # over: a record is made while nothing is recorded, and never changed afterwards.
    in_time = datetime.now(UTC) + timedelta(seconds=30)
    await daemon_kit.change(type(row), placed.id, state="claimed", deadline=in_time)
    assert (await daemon.refuse(client, placed.id, "busy")).json() == {"id": placed.id, "recorded": "answer"}
    assert (await daemon.answer(client, call)).json() == {"id": placed.id, "recorded": "answer"}
    kept = await daemon_kit.call_row(placed.id)
    assert (kept.answer_sealed, kept.recorded, kept.refusal) == (first, "answer", None)


async def test_a_refusal_is_recorded_once_with_a_code_of_the_closed_list(
    client: AsyncClient, daemon: Daemon
) -> None:
    from choregos_api.outposts import rendezvous

    placed = await daemon_kit.place(write=True, tool="create_issue")
    (call,) = await daemon.calls(client)
    # A code outside the closed list is a 422: a refusal names a class and never the rule.
    for body in ({"code": "the_rule_42_of_my_policy"}, {"code": "busy", "why": "x"}, {}, {"code": None}):
        refused(await daemon.send(client, refuse_path(placed.id), body), "invalid_request", 422)
    assert (await daemon_kit.call_row(placed.id)).recorded is None
    response = await daemon.refuse(client, placed.id, "write_denied")
    assert (response.status_code, response.json()) == (200, {"id": placed.id, "recorded": "refusal"})
    daemon_kit.is_a("CallAck", response.json())
    row = await daemon_kit.call_row(placed.id)
    assert (row.state, row.recorded, row.refusal, row.recorded_by) == (
        "refused",
        "refusal",
        "write_denied",
        daemon.instance_id,
    )
    assert row.arguments_ciphertext is None and row.answer_key_ciphertext is None
    # Sent again, and an answer after it: what was recorded.
    assert (await daemon.refuse(client, placed.id, "busy")).json()["recorded"] == "refusal"
    assert (await daemon.answer(client, call)).json() == {"id": placed.id, "recorded": "refusal"}
    assert (await daemon_kit.call_row(placed.id)).refusal == "write_denied"
    outcome = await rendezvous.wait(row.outpost_id, placed.id)
    assert outcome is not None and (outcome.state.value, outcome.refusal, outcome.answer) == (
        "refused",
        "write_denied",
        None,
    )


async def test_every_code_of_the_contracts_list_is_taken_and_no_other(
    client: AsyncClient, daemon: Daemon
) -> None:
    """The list is the contract's, read from it and written nowhere in the door: a code the contract
    gains is taken the day it lands."""
    import inspect

    from choregos_api.outposts import auth
    from choregos_api.outposts import door as door_module

    codes = daemon_kit.contracts.load_schema("outpost-refusal.schema.json")["properties"]["code"]["enum"]
    assert set(codes) == {code.value for code in outpost.OutpostRefusalCode}
    for module in (auth, door_module):
        source = inspect.getsource(module)
        assert not [code for code in codes if f'"{code}"' in source], f"{module.__name__} spells a code out"
    placed = [(await daemon_kit.place()).id for _ in codes]
    assert len(await daemon.calls(client, capacity=64)) == len(codes)
    for call_id, code in zip(placed, codes, strict=True):
        assert (await daemon.refuse(client, call_id, code)).json() == {"id": call_id, "recorded": "refusal"}
        assert (await daemon_kit.call_row(call_id)).refusal == code


async def test_an_answer_to_another_outposts_call_is_a_404_never_a_403(
    client: AsyncClient, daemon: Daemon
) -> None:
    """Whatever is not this outpost's does not exist for it: the answer does not say whether a call,
    an instance or an outpost exists (ADR 0148 §6)."""
    other = await daemon_kit.approved(client, "cold-store")
    theirs = await daemon_kit.place("cold-store")
    (call,) = await other.calls(client)
    mine = await daemon_kit.place()
    never = refused(
        await daemon.answer(client, {"id": UNKNOWN_CALL, "answer_key": call["answer_key"]}), "not_found", 404
    )
    stolen = await daemon.answer(client, call)
    # The same answer, word for word: nothing in it says that this call exists.
    same = {"instance": None, "server_time": None}
    assert refused(stolen, "not_found", 404) | same == never | same
    assert stolen.headers[NONCE_HEADER]
    refused(await daemon.refuse(client, theirs.id), "not_found", 404)
    # Its own call it has not claimed is not its to answer either, and that one exists: a conflict.
    refused(await daemon.refuse(client, mine.id), "lease_lost", 409)
    assert (await daemon_kit.call_row(theirs.id, "cold-store")).recorded is None
    assert (await other.answer(client, call)).status_code == 200


async def test_a_refusal_for_a_call_the_door_does_not_know_is_a_404_without_an_alert(
    client: AsyncClient, daemon: Daemon
) -> None:
    """What a forged call produces: a daemon refuses it `signature_refused`, for an id the door never
    handed over. Harmless: a 404, and nothing is raised."""
    before = await _door_events()
    for call_id in (UNKNOWN_CALL, "x", "a" * 128):
        refused(await daemon.refuse(client, call_id, "signature_refused"), "not_found", 404)
    refused(await daemon.refuse(client, "not..a..call..id", "signature_refused"), "not_found", 404)
    assert await _door_events() == before


async def test_an_answer_is_taken_only_from_the_instance_that_claimed_the_call(
    client: AsyncClient, daemon: Daemon
) -> None:
    other = await daemon_kit.approved(client, create=False)
    placed = await daemon_kit.place()
    (call,) = await daemon.calls(client)
    assert await other.calls(client) == [], "claimed, it is not handed to another instance"
    # Another instance of the same outpost holds the answer key if it was told the call; not the lease.
    refused(await other.answer(client, call), "lease_lost", 409)
    refused(await other.refuse(client, placed.id), "lease_lost", 409)
    assert (await daemon_kit.call_row(placed.id)).recorded is None
    assert (await daemon.answer(client, call)).status_code == 200
    # What one instance recorded, another does not read back.
    refused(await other.answer(client, call), "lease_lost", 409)


async def test_an_answer_after_the_deadline_is_refused(client: AsyncClient, daemon: Daemon) -> None:
    from choregos_api.db.models import OutpostCall

    placed = await daemon_kit.place()
    (call,) = await daemon.calls(client)
    await daemon_kit.change(OutpostCall, placed.id, deadline=datetime.now(UTC) - timedelta(seconds=1))
    refused(await daemon.answer(client, call), "lease_lost", 409)
    refused(await daemon.refuse(client, placed.id), "lease_lost", 409)
    assert (await daemon_kit.call_row(placed.id)).recorded is None


async def test_an_answer_under_a_previous_epoch_is_refused(client: AsyncClient, daemon: Daemon) -> None:
    """A revocation moves the outpost's epoch, and an answer is taken only under the epoch its call
    was claimed in: nothing in flight is taken, from the revoked instance or from another."""
    other = await daemon_kit.approved(client, create=False)
    read, write = await daemon_kit.place(), await daemon_kit.place(write=True, tool="create_issue")
    calls = {call["id"]: call for call in await daemon.calls(client)}
    answered = await daemon_kit.place()
    (early,) = await daemon.calls(client)
    assert (await daemon.answer(client, early)).status_code == 200
    assert (await client.post(f"{kit.BASE}/{SLUG}/instances/{other.instance_id}/revoke")).status_code == 200
    assert (await kit.outpost_row(SLUG)).epoch == 1
    for call_id in (read.id, write.id):
        refused(await daemon.answer(client, calls[call_id]), "lease_lost", 409)
        refused(await daemon.refuse(client, call_id), "lease_lost", 409)
        assert (await daemon_kit.call_row(call_id)).recorded is None
    # What it recorded under the former epoch it does not read back under the new one.
    refused(await daemon.answer(client, early), "lease_lost", 409)
    assert (await daemon_kit.call_row(answered.id)).recorded_epoch == 0
    # The poll says so: none of what it holds is awaited any more. What it claims now is under the new epoch.
    fresh = await daemon_kit.place()
    response = await daemon.poll(client, running=[read.id, write.id])
    assert response.json()["cancel"] == [read.id, write.id]
    (again,) = [daemon.opened(handed) for handed in response.json()["calls"]]
    assert (await daemon_kit.call_row(fresh.id)).epoch == 1
    assert (await daemon.answer(client, again)).status_code == 200


async def test_an_answer_above_the_cap_or_not_an_envelope_is_refused(
    client: AsyncClient, daemon: Daemon
) -> None:
    placed = await daemon_kit.place()
    (call,) = await daemon.calls(client)
    path = answer_path(placed.id)
    refused(
        await daemon.send(client, path, {"sealed": "A" * MAX_BODY_BYTES["answer"]}), "body_too_large", 413
    )
    for body in (
        {"sealed": "short"},
        {"sealed": "not base64url!" * 10},
        {"result": {}},
        {"sealed": "A" * 80, "x": 1},
    ):
        refused(await daemon.send(client, path, body), "invalid_request", 422)
    assert (await daemon_kit.call_row(placed.id)).recorded is None
    assert (await daemon.answer(client, call)).status_code == 200


async def test_every_problem_carries_the_nonce_its_status_is_documented_with(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Outpost-Nonce` goes on the answers that are taken and on 401, 403, 404 and 409, as the
    document's headers say; the daemon keeps the last one it received."""
    from choregos_api.outposts import auth

    document = daemon_kit.contracts.load_outpost_door()
    shared = document["components"]["responses"]

    def carries_the_nonce(answer: dict[str, Any]) -> bool:
        answer = shared[answer["$ref"].rsplit("/", 1)[-1]] if "$ref" in answer else answer
        return "Outpost-Nonce" in (answer.get("headers") or {})

    declared = {
        int(status)
        for verbs in document["paths"].values()
        for status, answer in verbs["post"]["responses"].items()
        if carries_the_nonce(answer)
    }
    assert declared == set(auth.NONCE_STATUSES) == {200, 201, 401, 403, 404, 409}

    waiting = await daemon_kit.place()
    pending = await daemon_kit.enrolled(client)
    too_much = b"x" * (MAX_BODY_BYTES["poll"] + 1)
    responses = [
        await daemon.poll(client, capacity=0),  # 200
        await client.post(POLL, content=b"{}"),  # 401
        await pending.poll(client),  # 403
        await daemon.refuse(client, UNKNOWN_CALL),  # 404
        await daemon.refuse(client, waiting.id),  # 409: its outpost's call, which it never claimed
        await client.post(POLL, content=too_much),  # 413
        await daemon.send(client, POLL, b"{}"),  # 422
    ]
    monkeypatch.setattr(auth, "PROTOCOL_MINIMUM", 2)
    responses.append(await daemon.poll(client))  # 426
    monkeypatch.setattr(auth, "PROTOCOL_MINIMUM", 1)
    kit.configure(monkeypatch, outposts_door_rate_per_minute="1")
    monkeypatch.setattr(auth, "_limiter", auth.RateLimiter(0))
    responses += [await daemon.poll(client), await daemon.poll(client)]  # 200, then 429
    seen = {response.status_code: NONCE_HEADER in response.headers for response in responses}
    assert seen == {
        200: True,
        401: True,
        403: True,
        404: True,
        409: True,
        413: False,
        422: False,
        426: False,
        429: False,
    }
