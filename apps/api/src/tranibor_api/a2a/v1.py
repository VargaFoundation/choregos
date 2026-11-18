# SPDX-License-Identifier: Apache-2.0
"""The door's A2A 1.0 spelling: the same calls as 0.3.0, read and answered in 1.0's shapes (ADR 0078).

The door decides in one place, in 0.3.0's terms: this module only translates on the way in and on the
way out, so every guard applies to a 1.0 call because it is the same call.

| | A2A 0.3.0 | A2A 1.0 |
| :-- | :-- | :-- |
| send | `message/send` | `SendMessage` (result `{"task": …}`) |
| stream | `message/stream` | `SendStreamingMessage` |
| read | `tasks/get` | `GetTask` |
| cancel | `tasks/cancel` | `CancelTask` |
| follow | `tasks/resubscribe` | `SubscribeToTask` |
| push | `tasks/pushNotificationConfig/set` … | `CreateTaskPushNotificationConfig` … |
| part | `{"kind": "file", "file": {"bytes", "mimeType", "name"}}` | `{"raw", "mediaType", "filename"}` |
| state | `input-required` | `TASK_STATE_INPUT_REQUIRED` |
| stream event | `{"kind": "status-update", "final": …}` | `{"statusUpdate": …}`; the end is the stream's |
| error `data` | an object | a list of details, a `google.rpc.ErrorInfo` first |

The method names of the two versions do not overlap, so the name says which one a call is in; an
`A2A-Version` header, when sent, must agree with it.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

V03 = "0.3"
V10 = "1.0"
#: The versions the door speaks, as an `A2A-Version` header names them (`Major.Minor`).
VERSIONS = (V03, V10)
HEADER = "A2A-Version"

#: A 1.0 method and the 0.3.0 method the door serves it as.
METHODS: dict[str, str] = {
    "SendMessage": "message/send",
    "SendStreamingMessage": "message/stream",
    "GetTask": "tasks/get",
    "CancelTask": "tasks/cancel",
    "SubscribeToTask": "tasks/resubscribe",
    "CreateTaskPushNotificationConfig": "tasks/pushNotificationConfig/set",
    "GetTaskPushNotificationConfig": "tasks/pushNotificationConfig/get",
    "ListTaskPushNotificationConfigs": "tasks/pushNotificationConfig/list",
    "DeleteTaskPushNotificationConfig": "tasks/pushNotificationConfig/delete",
}
#: 1.0 methods the door knows and does not serve: the answer says so instead of "unknown method".
UNSERVED = frozenset({"ListTasks", "GetExtendedAgentCard"})
#: 1.0's own errors beyond 0.3.0's (A2A 1.0 §9.5).
EXTENDED_CARD_NOT_CONFIGURED = -32007
VERSION_NOT_SUPPORTED = -32009
#: `ErrorInfo.domain` of the protocol's errors, and of the door's own (the budget, the streams cap).
DOMAIN = "a2a-protocol.org"
OWN_DOMAIN = "choregos.dev"
ERROR_INFO = "type.googleapis.com/google.rpc.ErrorInfo"
BAD_REQUEST = "type.googleapis.com/google.rpc.BadRequest"


def is_v1(method: str) -> bool:
    return method in METHODS or method in UNSERVED


def asked_version(request: Request) -> str | None:
    """The `A2A-Version` the client sent (header, else query parameter), as `Major.Minor`; `None` if empty."""
    raw = (request.headers.get(HEADER) or request.query_params.get(HEADER) or "").strip()
    if not raw:
        return None
    return ".".join(raw.split(".")[:2])


# ───────────────────────── on the way in ─────────────────────────


def params_in(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """A 1.0 request's params, as the 0.3.0 method reads them. Parts stay in 1.0: the door reads both."""
    if method in {"SendMessage", "SendStreamingMessage"}:
        translated = dict(params)
        configuration = params.get("configuration")
        if isinstance(configuration, dict) and "taskPushNotificationConfig" in configuration:
            translated["configuration"] = {
                **configuration,
                "pushNotificationConfig": configuration["taskPushNotificationConfig"],
            }
        return translated
    if method == "CreateTaskPushNotificationConfig":
        asked = {k: params[k] for k in ("id", "url", "token", "authentication") if k in params}
        return {"taskId": params.get("taskId"), "pushNotificationConfig": asked}
    if method in {"GetTaskPushNotificationConfig", "DeleteTaskPushNotificationConfig"}:
        return {"id": params.get("taskId"), "pushNotificationConfigId": params.get("id")}
    if method == "ListTaskPushNotificationConfigs":
        return {"id": params.get("taskId")}
    return params


# ───────────────────────── on the way out ─────────────────────────


def state_out(state: str) -> str:
    """`input-required` → `TASK_STATE_INPUT_REQUIRED`."""
    return "TASK_STATE_" + state.upper().replace("-", "_")


def part_out(part: dict[str, Any]) -> dict[str, Any]:
    """A part without `kind`: the field it carries says what it is (`text`, `data`, `raw`)."""
    kind = part.get("kind")
    if kind == "text":
        return {"text": part["text"]}
    if kind == "data":
        return {"data": part["data"]}
    file = part.get("file") or {}
    out: dict[str, Any] = {"raw": file.get("bytes", "")}
    if file.get("name"):
        out["filename"] = file["name"]
    if file.get("mimeType"):
        out["mediaType"] = file["mimeType"]
    return out


def message_out(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "messageId": message["messageId"],
        "taskId": message["taskId"],
        "contextId": message["contextId"],
        "role": "ROLE_AGENT",
        "parts": [part_out(p) for p in message["parts"]],
    }


def status_out(status: dict[str, Any]) -> dict[str, Any]:
    return {"state": state_out(status["state"]), "message": message_out(status["message"])}


def artifact_out(artifact: dict[str, Any]) -> dict[str, Any]:
    return {**artifact, "parts": [part_out(p) for p in artifact["parts"]]}


def task_out(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task["id"],
        "contextId": task["contextId"],
        "status": status_out(task["status"]),
        "artifacts": [artifact_out(a) for a in task["artifacts"]],
        "metadata": task["metadata"],
    }


def event_out(event: dict[str, Any]) -> dict[str, Any]:
    """One item of a stream, a `StreamResponse`: the task, a status update or an artefact update.

    1.0 has no `final`: the stream's end is the task's."""
    kind = event.get("kind")
    if kind == "status-update":
        update = {k: v for k, v in event.items() if k not in {"kind", "final"}}
        return {"statusUpdate": {**update, "status": status_out(event["status"])}}
    if kind == "artifact-update":
        update = {k: v for k, v in event.items() if k != "kind"}
        return {"artifactUpdate": {**update, "artifact": artifact_out(event["artifact"])}}
    return {"task": task_out(event)}


def push_config_out(view: dict[str, Any]) -> dict[str, Any]:
    """A flat `TaskPushNotificationConfig`; the door's `signing` (on create) and `metadata` stay beside it."""
    config = view["pushNotificationConfig"]
    out = {"id": config["id"], "taskId": view["taskId"], "url": config["url"], "metadata": view["metadata"]}
    if "signing" in view:
        out["signing"] = view["signing"]
    return out


def result_out(method: str, result: Any) -> Any:
    """The 0.3.0 answer of the method a 1.0 call was served as, in 1.0's shape."""
    if method == "SendMessage":
        return {"task": task_out(result)}
    if method in {"GetTask", "CancelTask"}:
        return task_out(result)
    if method in {"CreateTaskPushNotificationConfig", "GetTaskPushNotificationConfig"}:
        return push_config_out(result)
    if method == "ListTaskPushNotificationConfigs":
        return {"configs": [push_config_out(view) for view in result], "nextPageToken": ""}
    if method == "DeleteTaskPushNotificationConfig":
        return {}
    return result


def error_data(reason: str, domain: str, data: Any = None) -> list[dict[str, Any]]:
    """1.0's `error.data`: an `ErrorInfo`, then a `BadRequest` naming the fields a workflow refused.

    `ErrorInfo.metadata` holds strings only: the console's `decision_url` fits, a list of errors does not."""
    info: dict[str, Any] = {"@type": ERROR_INFO, "reason": reason, "domain": domain}
    extra = data if isinstance(data, dict) else {}
    strings = {k: v for k, v in extra.items() if isinstance(v, str)}
    if strings:
        info["metadata"] = strings
    details = [info]
    errors = extra.get("errors")
    if isinstance(errors, list) and errors:
        details.append(
            {
                "@type": BAD_REQUEST,
                "fieldViolations": [
                    {
                        "field": ".".join(str(p) for p in e.get("loc") or []),
                        "description": str(e.get("msg") or ""),
                    }
                    for e in errors
                    if isinstance(e, dict)
                ],
            }
        )
    return details
