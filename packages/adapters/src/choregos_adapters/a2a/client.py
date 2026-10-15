# SPDX-License-Identifier: Apache-2.0
"""A thin A2A client over httpx (ADR 0062): the four calls a step needs, in 0.3 and 1.0 spelling.

| | A2A 0.3 | A2A 1.0 |
| :-- | :-- | :-- |
| send | `message/send` | `SendMessage` |
| stream | `message/stream` | `SendStreamingMessage` |
| get | `tasks/get` | `GetTask` |
| cancel | `tasks/cancel` | `CancelTask` |

The card's `protocolVersion` picks the spelling. 1.0 details come from secondary sources (see the
ADR): the parsers read both shapes and the 1.0 path is exercised against the fake only.

What it does not do: push notifications (Atlassian's gateway forces them off), the extended card,
gRPC and HTTP+JSON transports, signed-card verification.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "rejected"})
#: States after which the task cannot go on without somebody else.
INTERRUPTED_STATES = frozenset({"input-required", "auth-required"})

_METHODS = {
    0: {"send": "message/send", "stream": "message/stream", "get": "tasks/get", "cancel": "tasks/cancel"},
    1: {"send": "SendMessage", "stream": "SendStreamingMessage", "get": "GetTask", "cancel": "CancelTask"},
}


class A2aError(RuntimeError):
    """The remote agent answered an error, or something that is not A2A."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class A2aAuthRequired(A2aError):  # noqa: N818 - the remote's own word: the caller must authenticate
    """HTTP 401/403, or a task in the `auth-required` state: the credential is not (or no longer) good."""


class A2aMethodNotSupported(A2aError):  # noqa: N818 - JSON-RPC -32601
    """The remote does not implement the method (e.g. streaming)."""


@dataclass(frozen=True)
class A2aPart:
    kind: str  # text | file | data
    text: str = ""
    name: str | None = None
    mime_type: str | None = None
    content: bytes | None = None
    uri: str | None = None
    data: Any = None


@dataclass
class A2aArtifact:
    artifact_id: str
    name: str
    parts: list[A2aPart] = field(default_factory=list)

    def text(self) -> str:
        return "".join(p.text for p in self.parts if p.kind == "text")


@dataclass
class A2aTask:
    id: str
    context_id: str
    state: str
    #: The text of the status message: the answer when completed, the question when input-required.
    message: str = ""
    message_parts: list[A2aPart] = field(default_factory=list)
    artifacts: list[A2aArtifact] = field(default_factory=list)
    final: bool = False

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def interrupted(self) -> bool:
        return self.state in INTERRUPTED_STATES


def normalise_state(state: Any) -> str:
    """`TASK_STATE_COMPLETED` (1.0), `completed` and `input_required` all read the same."""
    text = str(state or "unknown").lower()
    text = text.removeprefix("task_state_").replace("_", "-")
    return "canceled" if text == "cancelled" else text


def parse_part(raw: dict[str, Any]) -> A2aPart:
    kind = str(raw.get("kind") or raw.get("type") or "")
    if kind == "text" or ("text" in raw and not kind):
        return A2aPart("text", text=str(raw.get("text") or ""))
    if kind == "file" or "file" in raw:
        file = raw.get("file") or raw
        content = None
        if file.get("bytes"):
            try:
                content = base64.b64decode(file["bytes"])
            except (ValueError, TypeError):
                content = None
        return A2aPart(
            "file",
            name=file.get("name"),
            mime_type=file.get("mimeType") or file.get("mediaType"),
            content=content,
            uri=file.get("uri"),
        )
    if kind == "data" or "data" in raw:
        return A2aPart("data", data=raw.get("data"))
    return A2aPart("data", data=raw)


def _parts(raw: Any) -> list[A2aPart]:
    return [parse_part(p) for p in (raw or []) if isinstance(p, dict)]


def _artifact(raw: dict[str, Any]) -> A2aArtifact:
    artifact_id = str(raw.get("artifactId") or raw.get("artifact_id") or raw.get("name") or uuid.uuid4().hex)
    return A2aArtifact(artifact_id, str(raw.get("name") or artifact_id), _parts(raw.get("parts")))


def parse_task(raw: dict[str, Any]) -> A2aTask:
    """A `Task`, or a bare `Message` (an agent that answers at once): the latter is a completed task."""
    kind = raw.get("kind")
    if kind == "message" or ("parts" in raw and "status" not in raw):
        parts = _parts(raw.get("parts"))
        return A2aTask(
            id=str(raw.get("taskId") or raw.get("messageId") or ""),
            context_id=str(raw.get("contextId") or ""),
            state="completed",
            message="".join(p.text for p in parts if p.kind == "text"),
            message_parts=parts,
            final=True,
        )
    status = raw.get("status") or {}
    message = status.get("message") or {}
    parts = _parts(message.get("parts"))
    return A2aTask(
        id=str(raw.get("id") or raw.get("taskId") or ""),
        context_id=str(raw.get("contextId") or ""),
        state=normalise_state(status.get("state")),
        message="".join(p.text for p in parts if p.kind == "text"),
        message_parts=parts,
        artifacts=[_artifact(a) for a in raw.get("artifacts") or [] if isinstance(a, dict)],
        final=bool(raw.get("final")),
    )


@dataclass
class A2aEvent:
    """One item of a stream: a task snapshot, a status change, or an artifact chunk."""

    kind: str  # task | status | artifact
    task_id: str = ""
    context_id: str = ""
    state: str | None = None
    message: str = ""
    final: bool = False
    task: A2aTask | None = None
    artifact: A2aArtifact | None = None
    append: bool = False


def parse_event(result: dict[str, Any]) -> A2aEvent:
    kind = result.get("kind")
    if kind == "status-update" or "statusUpdate" in result:
        body = result.get("statusUpdate", result)
        status = body.get("status") or {}
        parts = _parts((status.get("message") or {}).get("parts"))
        return A2aEvent(
            "status",
            task_id=str(body.get("taskId") or ""),
            context_id=str(body.get("contextId") or ""),
            state=normalise_state(status.get("state")),
            message="".join(p.text for p in parts if p.kind == "text"),
            final=bool(body.get("final")),
        )
    if kind == "artifact-update" or "artifactUpdate" in result:
        body = result.get("artifactUpdate", result)
        return A2aEvent(
            "artifact",
            task_id=str(body.get("taskId") or ""),
            context_id=str(body.get("contextId") or ""),
            artifact=_artifact(body.get("artifact") or {}),
            append=bool(body.get("append")),
        )
    if "task" in result and isinstance(result["task"], dict):
        result = result["task"]
    if "message" in result and isinstance(result["message"], dict) and "status" not in result:
        result = result["message"]
    task = parse_task(result)
    return A2aEvent("task", task_id=task.id, context_id=task.context_id, state=task.state, task=task)


def text_part(text: str) -> dict[str, Any]:
    return {"kind": "text", "text": text}


def file_part(name: str, content: bytes, mime_type: str = "application/octet-stream") -> dict[str, Any]:
    return {
        "kind": "file",
        "file": {"name": name, "mimeType": mime_type, "bytes": base64.b64encode(content).decode("ascii")},
    }


class A2aClient:
    """One remote agent's endpoint. The token is the caller's: it is sent, never logged or returned."""

    def __init__(
        self,
        endpoint: str,
        token: str | None,
        protocol_version: str = "0.3.0",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.endpoint = endpoint
        self._token = token
        major = protocol_version.split(".", 1)[0]
        self._methods = _METHODS[1 if major.isdigit() and int(major) >= 1 else 0]
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> A2aClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _headers(self, *, stream: bool = False) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def _body(method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}

    @staticmethod
    def message(
        parts: list[dict[str, Any]],
        *,
        context_id: str | None = None,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "kind": "message",
            "role": "user",
            "messageId": uuid.uuid4().hex,
            "parts": parts,
        }
        if context_id:
            message["contextId"] = context_id
        if task_id:
            message["taskId"] = task_id
        if metadata:
            message["metadata"] = metadata
        return message

    def _check_http(self, response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise A2aAuthRequired(f"the remote agent refused the credential (HTTP {response.status_code})")
        if response.status_code >= 400:
            raise A2aError(f"the remote agent answered HTTP {response.status_code}")

    @staticmethod
    def _unwrap(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise A2aError("the remote agent did not answer JSON-RPC")
        if payload.get("error"):
            error = payload["error"]
            code = error.get("code") if isinstance(error, dict) else None
            text = str(error.get("message") if isinstance(error, dict) else error)[:300]
            if code == -32601:
                raise A2aMethodNotSupported(text, code)
            raise A2aError(text, code)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise A2aError("the remote agent's answer has no result")
        return result

    async def _call(self, key: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.post(
                self.endpoint, json=self._body(self._methods[key], params), headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise A2aError(f"the remote agent is unreachable: {type(exc).__name__}") from exc
        self._check_http(response)
        try:
            return self._unwrap(response.json())
        except ValueError as exc:
            raise A2aError("the remote agent did not answer JSON") from exc

    async def send(self, message: dict[str, Any], *, blocking: bool = False) -> A2aTask:
        """`message/send`, non-blocking by default: a gateway may cut a long call, polling cannot be cut."""
        result = await self._call("send", {"message": message, "configuration": {"blocking": blocking}})
        return parse_task(result)

    async def get_task(self, task_id: str) -> A2aTask:
        return parse_task(await self._call("get", {"id": task_id}))

    async def cancel(self, task_id: str) -> A2aTask:
        return parse_task(await self._call("cancel", {"id": task_id}))

    async def stream(self, message: dict[str, Any]) -> AsyncIterator[A2aEvent]:
        """`message/stream`: server-sent events, each a JSON-RPC response (a task, a status, an artifact)."""
        try:
            async with self._http.stream(
                "POST",
                self.endpoint,
                json=self._body(self._methods["stream"], {"message": message}),
                headers=self._headers(stream=True),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    self._check_http(response)
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" not in content_type:
                    # A server that answers a plain JSON-RPC response (an error, or one task).
                    body = await response.aread()
                    yield parse_event(self._unwrap(json.loads(body)))
                    return
                data: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        yield parse_event(self._unwrap(json.loads("\n".join(data))))
                        data = []
                if data:
                    yield parse_event(self._unwrap(json.loads("\n".join(data))))
        except httpx.HTTPError as exc:
            raise A2aError(f"the remote agent is unreachable: {type(exc).__name__}") from exc
        except json.JSONDecodeError as exc:
            raise A2aError("the remote agent's stream carries something that is not JSON") from exc
