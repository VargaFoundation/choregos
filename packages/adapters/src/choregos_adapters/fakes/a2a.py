# SPDX-License-Identifier: Apache-2.0
"""A scriptable A2A agent for tests and `CHOREGOS_FAKES` (ADR 0005, ADR 0062).

It serves an Agent Card (GET) and a JSON-RPC endpoint (POST) over `httpx.MockTransport`
(`fake.transport()`), so the client, the runner and the orchestrator talk to it exactly as they
would talk to Atlassian's gateway — without a network. Scriptable: the states a task goes through,
its artifacts, a question (`input-required`), a failure, a bearer token or an API key it demands, a
streaming or polling-only agent, a card that changes between two fetches, and a slow agent (a fake
clock).

Every request is recorded in `fake.requests` (method, params, whether the call carried a token — the
token itself is kept apart in `fake.tokens`, so a test can assert it never reaches a journal).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

from ..a2a.client import _METHODS

HOST = "https://agent.remote.test"
CARD_PATH = "/.well-known/agent.json"


@dataclass
class FakeA2aAgent:
    protocol_version: str = "0.3.0"
    #: The states a task reports, in order. The last one is final (`completed`, `failed`, `input-required`…).
    timeline: list[str] = field(default_factory=lambda: ["working", "completed"])
    answer: str = "done"
    #: `(name, text)` artifacts delivered with the final state.
    artifacts: list[tuple[str, str]] = field(default_factory=list)
    #: A `data` part delivered as an artifact: `(name, json value)`.
    data_artifacts: list[tuple[str, Any]] = field(default_factory=list)
    skills: list[str] = field(default_factory=lambda: ["work-in-jira", "work-in-confluence"])
    streaming: bool = True
    #: When set, the endpoint refuses (401) any call without this token where the card asks for it:
    #: `Authorization: Bearer <token>`, or `<api_key_header>: <token>` for an `api_key` card.
    required_token: str | None = None
    #: The security scheme the card advertises: `oauth2`, `bearer`, `api_key` or `none`.
    security: str = "oauth2"
    #: The header an `api_key` card names (ServiceNow's is `x-sn-apikey`).
    api_key_header: str = "X-API-Key"
    host: str = HOST
    #: Raised for the method that is called: `{"stream": -32601}` makes streaming unsupported.
    method_errors: dict[str, int] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)
    tokens: list[str | None] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    card_fetches: int = 0
    #: After this many card fetches, the card changes (its description): drift.
    drift_after: int | None = None
    #: What the authorisation server side received: `(path, form or json)`, for the credential tests.
    oauth_requests: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    #: A refresh token this fake still honours; each refresh replaces it (rotation, like Atlassian's).
    live_refresh_token: str | None = None
    _issued: int = 0
    _polls: dict[str, int] = field(default_factory=dict)

    # ── addresses ───────────────────────────────────────────────────────────────────────────
    @property
    def card_url(self) -> str:
        return f"{self.host}{CARD_PATH}"

    @property
    def endpoint(self) -> str:
        return f"{self.host}/v1/rpc"

    # ── documents ───────────────────────────────────────────────────────────────────────────
    def card_document(self) -> dict[str, Any]:
        card: dict[str, Any] = {
            "name": "Fake remote agent",
            "description": "A scriptable A2A agent."
            + (" (changed)" if self.drift_after is not None and self.card_fetches > self.drift_after else ""),
            "url": self.endpoint,
            "version": "1.0.0",
            "protocolVersion": self.protocol_version,
            "capabilities": {"streaming": self.streaming, "pushNotifications": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [{"id": s, "name": s, "description": f"the {s} skill"} for s in self.skills],
            "preferredTransport": "JSONRPC",
        }
        if self.security == "oauth2":
            card["securitySchemes"] = {
                "oauth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": f"{self.host}/authorize",
                            "tokenUrl": f"{self.host}/token",
                            "scopes": {"chat": "chat with the agent", "offline_access": "refresh"},
                        }
                    },
                }
            }
            card["security"] = [{"oauth2": ["chat", "offline_access"]}]
            card["capabilities"]["extensions"] = [
                {
                    "uri": "https://example.test/setup-dcr",
                    "params": {"target_url": f"{self.host}/dcr/register"},
                }
            ]
        elif self.security == "bearer":
            card["securitySchemes"] = {"token": {"type": "http", "scheme": "bearer"}}
            card["security"] = [{"token": []}]
        elif self.security == "api_key":
            card["securitySchemes"] = {"key": {"type": "apiKey", "in": "header", "name": self.api_key_header}}
            card["security"] = [{"key": []}]
        return card

    def _task(self, task_id: str, state: str, context_id: str, *, with_artifacts: bool) -> dict[str, Any]:
        text = self.answer
        task: dict[str, Any] = {
            "kind": "task",
            "id": task_id,
            "contextId": context_id,
            "status": {
                "state": state,
                "message": {
                    "kind": "message",
                    "role": "agent",
                    "messageId": uuid.uuid4().hex,
                    "parts": [{"kind": "text", "text": text}],
                },
            }
            if state in {"completed", "input-required", "failed"}
            else {"state": state},
        }
        if with_artifacts and state == "completed":
            task["artifacts"] = self._artifacts()
        return task

    def _artifacts(self) -> list[dict[str, Any]]:
        out = [
            {"artifactId": name, "name": name, "parts": [{"kind": "text", "text": text}]}
            for name, text in self.artifacts
        ]
        out += [
            {"artifactId": name, "name": name, "parts": [{"kind": "data", "data": value}]}
            for name, value in self.data_artifacts
        ]
        return out

    # ── transport ───────────────────────────────────────────────────────────────────────────
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == CARD_PATH:
            self.card_fetches += 1
            return httpx.Response(200, json=self.card_document())
        if request.method == "POST" and request.url.path in {"/dcr/register", "/token"}:
            return self._oauth(request)
        if request.method != "POST" or request.url.path != "/v1/rpc":
            return httpx.Response(404)
        auth = request.headers.get("authorization")
        if self.security == "api_key":
            token = request.headers.get(self.api_key_header)
        else:
            token = auth.removeprefix("Bearer ") if auth and auth.startswith("Bearer ") else None
        self.tokens.append(token)
        if self.required_token is not None and token != self.required_token:
            return httpx.Response(401, json={"error": "unauthorized"})
        body = json.loads(request.content)
        names = {v: k for k, v in _METHODS[1 if self.protocol_version.startswith("1") else 0].items()}
        key = names.get(body["method"])
        self.requests.append(
            {
                "method": body["method"],
                "params": body.get("params", {}),
                "has_token": token is not None,
                "has_authorization": auth is not None,
            }
        )
        if key is None or key in self.method_errors:
            code = self.method_errors.get(key or "", -32601)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "error": {"code": code, "message": "no such method"},
                },
            )
        params = body.get("params", {})
        if key == "send":
            message = params["message"]
            task_id = message.get("taskId") or uuid.uuid4().hex
            context_id = message.get("contextId") or uuid.uuid4().hex
            self._polls[task_id] = 0
            return self._rpc(body, self._task(task_id, self.timeline[0], context_id, with_artifacts=True))
        if key == "get":
            task_id = params["id"]
            self._polls[task_id] = min(self._polls.get(task_id, 0) + 1, len(self.timeline) - 1)
            return self._rpc(
                body, self._task(task_id, self.timeline[self._polls[task_id]], "ctx", with_artifacts=True)
            )
        if key == "cancel":
            self.cancelled.append(params["id"])
            return self._rpc(body, self._task(params["id"], "canceled", "ctx", with_artifacts=False))
        # stream
        message = params["message"]
        task_id = uuid.uuid4().hex
        context_id = message.get("contextId") or uuid.uuid4().hex
        chunks = []
        for index, state in enumerate(self.timeline):
            last = index == len(self.timeline) - 1
            if last and state == "completed":
                for artifact in self._artifacts():
                    chunks.append(
                        {
                            "kind": "artifact-update",
                            "taskId": task_id,
                            "contextId": context_id,
                            "artifact": artifact,
                            "lastChunk": True,
                        }
                    )
            status: dict[str, Any] = {"state": state}
            if last and state in {"completed", "input-required", "failed"}:
                status["message"] = {
                    "kind": "message",
                    "role": "agent",
                    "messageId": uuid.uuid4().hex,
                    "parts": [{"kind": "text", "text": self.answer}],
                }
            chunks.append(
                {
                    "kind": "status-update",
                    "taskId": task_id,
                    "contextId": context_id,
                    "status": status,
                    "final": last,
                }
            )
        text = "".join(
            f"data: {json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': c})}\n\n" for c in chunks
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=text.encode())

    def _oauth(self, request: httpx.Request) -> httpx.Response:
        """Dynamic Client Registration and the token endpoint, with a rotating refresh token."""
        content_type = request.headers.get("content-type", "")
        if "json" in content_type:
            payload: dict[str, Any] = json.loads(request.content)
        else:
            payload = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.oauth_requests.append((request.url.path, payload))
        if request.url.path == "/dcr/register":
            return httpx.Response(
                201, json={"client_id": "fake-client-id", "redirect_uris": payload.get("redirect_uris", [])}
            )
        grant = payload.get("grant_type")
        if grant == "authorization_code":
            if not payload.get("code_verifier") or not payload.get("code"):
                return httpx.Response(400, json={"error": "invalid_request"})
        elif grant == "refresh_token":
            if payload.get("refresh_token") != self.live_refresh_token:
                return httpx.Response(400, json={"error": "invalid_grant"})
        else:
            return httpx.Response(400, json={"error": "unsupported_grant_type"})
        self._issued += 1
        self.live_refresh_token = f"rt-{self._issued}"
        return httpx.Response(
            200,
            json={
                "access_token": f"at-{self._issued}",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": self.live_refresh_token,
                "scope": "chat offline_access",
            },
        )

    @staticmethod
    def _rpc(body: dict[str, Any], result: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    # ── the OAuth side, for the credential tests ───────────────────────────────────────────
    def asgi_app(self) -> Any:
        """The same agent as a tiny ASGI app, for an end-to-end run that serves it on a port."""

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            assert scope["type"] == "http"
            body = b""
            while True:
                event = await receive()
                body += event.get("body", b"")
                if not event.get("more_body"):
                    break
            headers = [(k.decode(), v.decode()) for k, v in scope["headers"]]
            request = httpx.Request(
                scope["method"], f"{self.host}{scope['path']}", headers=headers, content=body
            )
            response = self._handle(request)
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": [
                        (k.encode(), v.encode())
                        for k, v in response.headers.items()
                        if k.lower() != "content-length"
                    ],
                }
            )
            await send({"type": "http.response.body", "body": response.content})

        return app
