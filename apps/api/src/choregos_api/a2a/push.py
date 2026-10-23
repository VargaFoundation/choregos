# SPDX-License-Identifier: Apache-2.0
"""Push notifications of the A2A door (ADR 0078 §5, S40-23d).

`tasks/pushNotificationConfig/set|get|list|delete` register a URL to hear of a task. The host must be
open in the organisation's egress list (ADR 0066) when it is registered; the signing secret is drawn
then, sealed in the secret store, and shown once, in the answer to `set`. A `set` on an existing id
replaces the URL and draws a new secret: it is the only moment one is shown.

Delivery reads the `events` table, never the bus: an event is notified only once it is persisted. A
pass takes the latest event of the work item after the registration's cursor, reads the token again
(revoked, expired, or no longer able to read the task: the registration ends), reads the task from the
database and POSTs it, signed, with the event id as the receiver's idempotency key. The cursor moves
only on a 2xx: a failed call is sent again on a later pass, with a growing delay, eight times at most.
Events in between are coalesced: the receiver gets the task as it is, not each step. An event that
changes nothing the task says moves the cursor without a call. Once the final state is delivered the
registration ends.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta
from typing import Any

import httpx
from choregos_core import utcnow
from choregos_core.egress import EgressRefusedError, host_port_of
from choregos_core.mcp import METHODE_INCONNUE, PARAMETRES_INVALIDES
from choregos_core.secrets import SecretIntrouvable
from sqlalchemy import exists, func, or_, select

from ..config import get_settings
from ..db.models import A2aPushConfig, ApiToken, Event, Organization
from ..db.session import TOUT, session_scope
from ..logging import get_logger
from ..mcp.appelant import SCOPES_A2A, Refus, caller_of_token
from ..secret_store import SecretStoreUnavailable, get_secret_store
from .door import PUSH_NOT_SUPPORTED, TASK_NOT_FOUND, Door, Fault, _task, task_of

log = get_logger("choregos.a2a.push")

#: Registrations per task and client: a few receivers, not a fan-out service.
MAX_CONFIGS = 5
MAX_URL = 2000
MAX_TOKEN = 500
MAX_ID = 200
INTERVAL_S = 5.0
BATCH = 100
#: A receiver that fails this many times in a row is given up; the task can still be read.
MAX_ATTEMPTS = 8
TIMEOUT_S = 10.0
#: `t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">`: the receiver checks it and the age of `t`.
SIGNATURE = "X-Choregos-Signature"
EVENT_ID = "X-Choregos-Event-Id"
#: A2A's header for the token the client gave at registration.
CLIENT_TOKEN = "X-A2A-Notification-Token"  # noqa: S105 - a header name
TERMINAL = frozenset({"completed", "failed", "canceled"})


def secret_name(config: A2aPushConfig) -> str:
    return f"a2a/push/{config.id}"


def sign(secret: str, stamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()


# ───────────────────────── the four methods ─────────────────────────


async def configure(door: Door, method: str, params: dict[str, Any]) -> Any:
    if door.caller.jeton is None:
        # An OAuth access token cannot be read again at delivery: nothing would stop the calls.
        raise Fault(PUSH_NOT_SUPPORTED, "push notifications need a `chg_` token with the a2a scope")
    action = method.removeprefix("tasks/pushNotificationConfig/")
    if action == "set":
        return await _set(door, params)
    if action == "get":
        return _view(await _one(door, params))
    if action == "list":
        item, _ = await _task(door, params.get("id"))
        return [_view(config) for config in await _configs(door, item.id)]
    if action == "delete":
        config = await _one(door, params)
        await _forget(door, config)
        return None
    raise Fault(METHODE_INCONNUE, f"unknown method: {method}")


async def _set(door: Door, params: dict[str, Any]) -> dict[str, Any]:
    item, _ = await _task(door, params.get("taskId"))
    asked = params.get("pushNotificationConfig")
    if not isinstance(asked, dict):
        raise Fault(PARAMETRES_INVALIDES, "`pushNotificationConfig` is an object")
    url = asked.get("url")
    target = host_port_of(url) if isinstance(url, str) and len(url) <= MAX_URL else None
    if target is None or not str(url).startswith("https://"):
        raise Fault(PARAMETRES_INVALIDES, f"`url` is an https URL of {MAX_URL} characters at most")
    if asked.get("authentication"):
        raise Fault(
            PARAMETRES_INVALIDES,
            "`authentication` is not taken: a notification carries your `token` and an HMAC signature",
        )
    token = asked.get("token")
    if token is not None and (not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN):
        raise Fault(PARAMETRES_INVALIDES, f"`token` is a string of {MAX_TOKEN} characters at most")
    config_id = asked.get("id") or item.id
    if not isinstance(config_id, str) or len(config_id) > MAX_ID:
        raise Fault(PARAMETRES_INVALIDES, f"`id` is a string of {MAX_ID} characters at most")
    await _open_in_egress(door, *target)
    known = {config.config_id: config for config in await _configs(door, item.id)}
    config = known.get(config_id)
    if config is None:
        if len(known) >= MAX_CONFIGS:
            raise Fault(PARAMETRES_INVALIDES, f"{MAX_CONFIGS} push notification configs per task at most")
        assert door.caller.jeton is not None
        config = A2aPushConfig(
            org_id=door.org.id,
            work_item_id=item.id,
            api_token_id=door.caller.jeton.id,
            client=door.caller.cle,
            config_id=config_id,
            url=url,
            attempts=0,
        )
        door.session.add(config)
        await door.session.flush()
    # A new or replaced registration starts again: the task as it is now is its first notification.
    config.url, config.attempts, config.finished_at = str(url), 0, None
    config.cursor_ts = config.cursor_event_id = config.last_view = config.next_attempt_at = None
    config.last_error = None
    signing = secrets.token_urlsafe(32)
    sealed = json.dumps({"secret": signing, "token": token})
    try:
        await get_secret_store().put(door.session, door.org.id, secret_name(config), sealed, door.caller.cle)
    except SecretStoreUnavailable as missing:
        raise Fault(
            PUSH_NOT_SUPPORTED, f"push notifications need the deployment's secrets key: {missing}"
        ) from missing
    return {
        **_view(config),
        # Shown here and never again: `get` and `list` do not return it.
        "signing": {"secret": signing, "header": SIGNATURE, "algorithm": "HMAC-SHA256", "event_id": EVENT_ID},
    }


async def _open_in_egress(door: Door, host: str, port: int) -> None:
    """The organisation's egress list decides at registration, as for any host the platform calls."""
    from ..services.egress import load_policy

    policy = await load_policy(door.session, door.org)
    try:
        policy.check(host, port, "api", "a2a.push")
    except EgressRefusedError as refused:
        raise Fault(
            PARAMETRES_INVALIDES,
            f"{host}:{port} is not open in the organisation's egress list ({refused.reason}): "
            "an administrator opens it (ADR 0066)",
        ) from refused


async def _configs(door: Door, item_id: str) -> list[A2aPushConfig]:
    """This client's registrations on the task: another client's are not seen."""
    return list(
        (
            await door.session.execute(
                select(A2aPushConfig)
                .where(A2aPushConfig.work_item_id == item_id, A2aPushConfig.client == door.caller.cle)
                .order_by(A2aPushConfig.created_at)
            )
        ).scalars()
    )


async def _one(door: Door, params: dict[str, Any]) -> A2aPushConfig:
    item, _ = await _task(door, params.get("id"))
    wanted = params.get("pushNotificationConfigId") or item.id
    for config in await _configs(door, item.id):
        if config.config_id == wanted:
            return config
    raise Fault(TASK_NOT_FOUND, f"no push notification config `{wanted}` on task {item.id}")


async def _forget(door: Door, config: A2aPushConfig) -> None:
    await get_secret_store().delete(door.session, door.org.id, secret_name(config))
    await door.session.delete(config)


def _view(config: A2aPushConfig) -> dict[str, Any]:
    return {
        "taskId": config.work_item_id,
        "pushNotificationConfig": {"id": config.config_id, "url": config.url},
        "metadata": {
            "choregos": {
                "delivered_event": config.cursor_event_id,
                "finished": config.finished_at is not None,
                "last_error": config.last_error,
            }
        },
    }


# ───────────────────────── delivery ─────────────────────────


async def deliver_pending(*, batch: int = BATCH) -> list[str]:
    """One pass: every live registration with an event after its cursor is notified.

    Returns the registrations a receiver accepted. Every replica runs it: the row is locked
    (`SKIP LOCKED`) and the cursor read again under the lock, so two replicas do not both call."""
    now = utcnow()
    newer = exists().where(
        Event.work_item_id == A2aPushConfig.work_item_id,
        or_(A2aPushConfig.cursor_ts.is_(None), Event.ts > A2aPushConfig.cursor_ts),
    )
    async with session_scope(orgs=TOUT) as session:
        due = list(
            (
                await session.execute(
                    select(A2aPushConfig.id)
                    .where(
                        A2aPushConfig.finished_at.is_(None),
                        or_(A2aPushConfig.next_attempt_at.is_(None), A2aPushConfig.next_attempt_at <= now),
                        newer,
                    )
                    .order_by(func.coalesce(A2aPushConfig.cursor_ts, A2aPushConfig.created_at))
                    .limit(batch)
                )
            ).scalars()
        )
    delivered: list[str] = []
    for config_id in due:
        async with session_scope(orgs=TOUT) as session:
            config = (
                await session.execute(
                    select(A2aPushConfig)
                    .where(A2aPushConfig.id == config_id, A2aPushConfig.finished_at.is_(None))
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if config is not None and await _deliver(session, config):
                delivered.append(config_id)
    return delivered


async def _deliver(session: Any, config: A2aPushConfig) -> bool:
    latest = (
        await session.execute(
            select(Event)
            .where(Event.work_item_id == config.work_item_id)
            .order_by(Event.ts.desc(), Event.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None or latest.id == config.cursor_event_id:
        return False
    org = await session.get(Organization, config.org_id)
    token = await session.get(ApiToken, config.api_token_id)
    try:
        if org is None or token is None:
            raise Refus(401, "unknown or revoked token")
        caller = await caller_of_token(session, token, scopes=SCOPES_A2A, door="A2A")
        door = Door(session, caller, org, get_settings().public_url.rstrip("/"), None)
        task = await task_of(door, *await _task(door, config.work_item_id))
    except (Refus, Fault) as gone:
        _end(config, f"the registration's token no longer reads the task: {gone}")
        return False
    view = json.dumps(
        [
            task["status"]["state"],
            task["metadata"]["choregos"]["state"],
            next(
                (
                    p["data"].get("request_id")
                    for p in task["status"]["message"]["parts"]
                    if p["kind"] == "data"
                ),
                None,
            ),
        ]
    )
    if view == config.last_view:
        config.cursor_ts, config.cursor_event_id = latest.ts, latest.id
        return False
    try:
        sealed = await get_secret_store().get_many(session, org.id, [secret_name(config)])
    except (SecretIntrouvable, SecretStoreUnavailable) as lost:
        _end(config, f"the signing secret cannot be read: {lost}")
        return False
    stored = json.loads(sealed[secret_name(config)])
    body = json.dumps(task, separators=(",", ":")).encode()
    stamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        SIGNATURE: f"t={stamp},v1={sign(stored['secret'], stamp, body)}",
        EVENT_ID: latest.id,
        "Idempotency-Key": latest.id,
    }
    if stored.get("token"):
        headers[CLIENT_TOKEN] = stored["token"]
    try:
        await _post(session, org, config.url, body, headers)
    except (httpx.HTTPError, EgressRefusedError, _Refused) as failure:
        _failed(config, f"{type(failure).__name__}: {failure}")
        return False
    config.cursor_ts, config.cursor_event_id, config.last_view = latest.ts, latest.id, view
    config.attempts, config.next_attempt_at, config.last_error = 0, None, None
    if task["status"]["state"] in TERMINAL:
        config.finished_at = utcnow()
    log.info(
        "a2a.push.delivered", work_item=config.work_item_id, event_id=latest.id, state=task["status"]["state"]
    )
    return True


class _Refused(Exception):  # noqa: N818 - the receiver's non-2xx answer
    pass


async def _post(session: Any, org: Organization, url: str, body: bytes, headers: dict[str, str]) -> None:
    """Through the organisation's egress guard, as every outbound call: a host closed since the
    registration is refused here too. No redirect is followed."""
    from ..services.egress import egress_scope
    from ..services.remote_agents import a2a_transport

    async with (
        egress_scope(session, org, "api", "a2a.push"),
        httpx.AsyncClient(transport=a2a_transport(), timeout=TIMEOUT_S, follow_redirects=False) as http,
    ):
        answer = await http.post(url, content=body, headers=headers)
    if not 200 <= answer.status_code < 300:
        raise _Refused(f"HTTP {answer.status_code}")


def _failed(config: A2aPushConfig, why: str) -> None:
    config.attempts += 1
    config.last_error = why[:400]
    if config.attempts >= MAX_ATTEMPTS:
        _end(config, f"given up after {config.attempts} failed calls: {why}")
        return
    config.next_attempt_at = utcnow() + timedelta(seconds=min(10 * 2**config.attempts, 3600))
    log.warning("a2a.push.failed", work_item=config.work_item_id, attempts=config.attempts, error=why[:200])


def _end(config: A2aPushConfig, why: str) -> None:
    config.finished_at, config.last_error = utcnow(), why[:400]
    log.warning("a2a.push.ended", work_item=config.work_item_id, reason=why[:200])


async def push_loop(interval_s: float = INTERVAL_S) -> None:
    """Background task of the API, like the action relay: a failed pass does not stop the API."""
    import asyncio

    while True:
        try:
            await deliver_pending()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("a2a.push.pass_failed", error=str(error)[:200])
        await asyncio.sleep(interval_s)
