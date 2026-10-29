# SPDX-License-Identifier: Apache-2.0
"""A step carried by a remote agent over A2A (ADR 0062): the pod calls the agent, waits, maps the answer.

The runner does what it does for every step — same pod, same run token, same inputs, same outputs,
same publication, same gates — except that no local agent starts. It:

1. reads the agent's card again and refuses on drift (`remote_card_changed`) BEFORE any message: the
   card digest was pinned when the version was published, and what answers now is not what was reviewed;
2. sends the instructions and the task as one text part (the declared inputs as file parts when the
   agent takes them), with `metadata.choregos = {run_id, work_item}`;
3. follows the task by streaming, or by polling when the agent cannot stream, until a final state or
   the step's time (then `tasks/cancel`);
4. maps the answer: the final text is `outputs/response.md`, every artifact a file or an inline output,
   facts from data parts are DECLARED (provenance `remote:<slug>`, never listed as measured).

Failures are results (`failed`, with a reason), not exits: the orchestrator reads them like any step's.
The access token is the run's, from its Secret; it is sent to the endpoint and nowhere else, as a
bearer token or, for an API key, in the header `RemoteCall.token_header` names.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
from choregos_adapters.a2a import (
    A2aArtifact,
    A2aAuthRequired,
    A2aClient,
    A2aError,
    A2aMethodNotSupported,
    A2aPart,
    A2aTask,
    CardError,
    fetch_card,
    file_part,
    text_part,
)
from choregos_contracts import StageInput, StageStatus
from choregos_core.redaction import REDACTED

from .inputs import INPUTS_DIR
from .outputs import OUTPUTS_DIR

#: The file a remote agent's final answer is written to, an artifact of the step.
RESPONSE_FILE = "response.md"
#: What `artifacts.reports` records, so that a result traces back to the card it ran against.
REPORT_ENDPOINT = "remote_endpoint"
REPORT_TASK = "remote_task_id"
REPORT_CARD = "remote_card_digest"
REPORT_PROVENANCE = "remote_provenance"
#: Bytes of inputs sent along as file parts: past that the step fails rather than posts a dump.
MAX_INPUT_BYTES = 5 * 1024 * 1024

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class RemoteOutcome:
    status: StageStatus
    reason: str | None
    summary: str
    state: str = ""
    task_id: str = ""
    context_id: str = ""
    response: str = ""
    questions: list[str] = field(default_factory=list)
    #: Inline outputs the agent returned by name (data or text artifacts matching a declared output).
    outputs: dict[str, Any] = field(default_factory=dict)
    facts: dict[str, str | int | float | bool] = field(default_factory=dict)
    duration_s: float = 0.0
    card_digest: str = ""


def failure(reason: str, summary: str, **extra: Any) -> RemoteOutcome:
    return RemoteOutcome(StageStatus.FAILED, reason, summary[:4000], **extra)


def safe_filename(name: str) -> str:
    """A file name an agent chose, made harmless: no path, no traversal, no hidden file."""
    base = Path(name.replace("\\", "/")).name
    cleaned = _SAFE_NAME.sub("_", base).lstrip(".")
    return cleaned[:120] or "artifact"


def _message_text(stage_input: StageInput, workspace: Path) -> str:
    """The instructions of the registry entry, then the task and the context the runner wrote."""
    pieces = [(stage_input.playbook.prompt or "").strip()]
    for name in ("task.md", "context.md"):
        path = workspace / ".choregos" / name
        if path.is_file():
            pieces.append(path.read_text(encoding="utf-8").strip())
    return "\n\n---\n\n".join(p for p in pieces if p)


def _input_parts(workspace: Path) -> list[dict[str, Any]]:
    folder = workspace / INPUTS_DIR
    parts: list[dict[str, Any]] = []
    total = 0
    if not folder.is_dir():
        return parts
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.is_symlink():
            continue
        content = path.read_bytes()
        total += len(content)
        if total > MAX_INPUT_BYTES:
            raise ValueError(f"the declared inputs exceed {MAX_INPUT_BYTES // (1024 * 1024)} MiB")
        parts.append(file_part(path.name, content))
    return parts


async def run_remote(
    stage_input: StageInput,
    workspace: Path,
    token: str,
    emit: Emit,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    poll_seconds: float = 5.0,
) -> RemoteOutcome:
    """Calls the remote agent and returns what it said, mapped. Never raises on the agent's failure."""
    remote = stage_input.remote
    assert remote is not None
    started = time.monotonic()

    # 1. the card, again: before any message leaves the pod.
    try:
        card = await fetch_card(remote.card_url, transport=transport)
    except CardError as exc:
        return failure("remote_card_unreachable", f"the remote agent's card could not be read: {exc}")
    if card.digest != remote.card_digest or card.url != remote.endpoint:
        await emit(
            "remote.card_changed",
            {"pinned": remote.card_digest, "now": card.digest, "endpoint": remote.endpoint},
        )
        return failure(
            "remote_card_changed",
            "the remote agent's card changed since this version was published "
            f"(pinned {remote.card_digest[:19]}…, now {card.digest[:19]}…): "
            "publish a new version to accept it",
            card_digest=card.digest,
        )

    # 2. the message
    parts = [text_part(_message_text(stage_input, workspace))]
    if remote.input == "text+files":
        try:
            parts += _input_parts(workspace)
        except ValueError as exc:
            return failure("remote_input_too_large", str(exc), card_digest=card.digest)
    metadata: dict[str, Any] = {
        "choregos": {
            "run_id": stage_input.run_id,
            "work_item": stage_input.work_item.key,
            **({"skill": remote.skill} if remote.skill else {}),
        }
    }
    await emit(
        "remote.started",
        {
            "endpoint": remote.endpoint,
            "skill": remote.skill,
            "card_digest": card.digest,
            "protocol_version": remote.protocol_version,
        },
    )

    client = A2aClient(
        remote.endpoint,
        token or None,
        remote.protocol_version,
        transport=transport,
        timeout=60.0,
        token_header=remote.token_header,
    )
    message = client.message(parts, metadata=metadata)
    task = A2aTask(id="", context_id="", state="submitted")
    try:
        async with asyncio.timeout(remote.timeout_s):
            task = await _drive(client, card.streaming, message, task, emit, poll_seconds)
    except TimeoutError:
        if task.id:
            with contextlib.suppress(A2aError):
                await client.cancel(task.id)
        outcome = failure(
            "remote_timeout",
            f"the remote agent did not finish within {remote.timeout_s} s: the task was cancelled",
            task_id=task.id,
            context_id=task.context_id,
            state=task.state,
            card_digest=card.digest,
        )
    except A2aAuthRequired as exc:
        outcome = failure(
            "remote_auth_required",
            f"{exc}: reconnect the credential in the administration",
            task_id=task.id,
            card_digest=card.digest,
        )
    except A2aError as exc:
        outcome = failure(
            "remote_failed", f"the remote agent failed: {exc}", task_id=task.id, card_digest=card.digest
        )
    else:
        outcome = _map(_scrubbed(task, token), stage_input, workspace, card.digest)
    finally:
        await client.aclose()
    outcome.duration_s = round(time.monotonic() - started, 2)
    await emit(
        "remote.finished",
        {
            "state": outcome.state or ("timeout" if outcome.reason == "remote_timeout" else "error"),
            "task_id": outcome.task_id,
            "duration_s": outcome.duration_s,
            "reason": outcome.reason,
        },
    )
    return outcome


async def _drive(
    client: A2aClient,
    streaming: bool,
    message: dict[str, Any],
    task: A2aTask,
    emit: Emit,
    poll_seconds: float,
) -> A2aTask:
    """Stream when the card says it can, else send and poll; returns the last state of the task."""
    last_state = ""

    async def seen(state: str) -> None:
        nonlocal last_state
        if state and state != last_state:
            last_state = state
            await emit("remote.update", {"state": state, "task_id": task.id})

    if streaming:
        try:
            artifacts: dict[str, A2aArtifact] = {}
            async for event in client.stream(message):
                task.id = event.task_id or task.id
                task.context_id = event.context_id or task.context_id
                if event.kind == "task" and event.task is not None:
                    task.artifacts = event.task.artifacts or task.artifacts
                    task.message = event.task.message or task.message
                    task.state = event.task.state
                elif event.kind == "status" and event.state:
                    task.state = event.state
                    task.message = event.message or task.message
                elif event.kind == "artifact" and event.artifact is not None:
                    held = artifacts.get(event.artifact.artifact_id)
                    if held is not None and event.append:
                        held.parts.extend(event.artifact.parts)
                    else:
                        artifacts[event.artifact.artifact_id] = event.artifact
                    task.artifacts = list(artifacts.values())
                await seen(task.state)
                if task.terminal or task.interrupted:
                    return task
            if task.id:  # the stream ended before a final state: ask
                return await _poll(client, task, seen, poll_seconds)
            return task
        except A2aMethodNotSupported:
            pass  # the card said streaming, the agent says otherwise: send and poll
    first = await client.send(message)
    task.id, task.context_id = first.id, first.context_id
    task.state, task.message, task.artifacts = first.state, first.message, first.artifacts
    await seen(task.state)
    if task.terminal or task.interrupted:
        return task
    return await _poll(client, task, seen, poll_seconds)


async def _poll(
    client: A2aClient, task: A2aTask, seen: Callable[[str], Awaitable[None]], poll_seconds: float
) -> A2aTask:
    while not (task.terminal or task.interrupted):
        await asyncio.sleep(poll_seconds)
        fresh = await client.get_task(task.id)
        task.state, task.message = fresh.state, fresh.message or task.message
        task.artifacts = fresh.artifacts or task.artifacts
        await seen(task.state)
    return task


def _scrubbed(task: A2aTask, token: str) -> A2aTask:
    """The task with the access token masked out of every text: a remote agent that echoes what it
    was given must not make the run's token a stored deliverable."""
    if not token:
        return task
    task.message = task.message.replace(token, REDACTED)
    for artifact in task.artifacts:
        artifact.parts = [
            replace(p, text=p.text.replace(token, REDACTED)) if p.kind == "text" else p
            for p in artifact.parts
        ]
    return task


def _map(task: A2aTask, stage_input: StageInput, workspace: Path, digest: str) -> RemoteOutcome:
    base: dict[str, Any] = {
        "state": task.state,
        "task_id": task.id,
        "context_id": task.context_id,
        "card_digest": digest,
    }
    if task.state == "auth-required":
        return failure(
            "remote_auth_required",
            "the remote agent asks to authenticate again: reconnect the credential in the administration",
            **base,
        )
    if task.state == "input-required":
        # Phase 1 (ADR 0062): the question is shown to a person, the step fails; no resume yet.
        question = task.message or "(the remote agent asked for more input without saying what)"
        return failure(
            "remote_input_required",
            f"the remote agent needs an answer: {question[:500]}",
            questions=[question],
            response=task.message,
            **base,
        )
    if task.state != "completed":
        detail = f": {task.message[:500]}" if task.message else ""
        return failure("remote_failed", f"the remote agent ended `{task.state}`{detail}", **base)

    outputs_dir = workspace / OUTPUTS_DIR
    outputs_dir.mkdir(parents=True, exist_ok=True)
    declared = {
        name: None
        for name in stage_input.transition.outputs
        if name not in stage_input.transition.file_outputs()
    }
    inline: dict[str, Any] = {}
    facts: dict[str, str | int | float | bool] = {}
    response = task.message
    for artifact in task.artifacts:
        _land(artifact, outputs_dir, declared, inline, facts)
    (outputs_dir / RESPONSE_FILE).write_text(response + ("\n" if response else ""), encoding="utf-8")
    # The single inline output nothing named takes the answer: a text-only agent has no other way
    # to fill it, and more than one unfilled leaves the step to fail on `missing_outputs`, honestly.
    unfilled = [name for name in declared if name not in inline]
    if len(unfilled) == 1 and response.strip():
        inline[unfilled[0]] = response
    summary = (response.strip().splitlines() or [f"remote agent `{stage_input.remote.agent_slug}` done"])[0]  # type: ignore[union-attr]
    return RemoteOutcome(
        StageStatus.DONE,
        None,
        summary[:4000],
        response=response,
        outputs=inline,
        facts=facts,
        **base,
    )


def _land(
    artifact: A2aArtifact,
    outputs_dir: Path,
    declared_inline: dict[str, Any],
    inline: dict[str, Any],
    facts: dict[str, str | int | float | bool],
) -> None:
    """One artifact of the remote agent: a file in `.choregos/outputs/`, an inline output, or facts."""
    for part in artifact.parts:
        if part.kind == "file" and part.content is not None:
            (outputs_dir / safe_filename(part.name or artifact.name)).write_bytes(part.content)
        elif part.kind == "text":
            _land_text(artifact, part, outputs_dir, declared_inline, inline)
        elif part.kind == "data":
            _land_data(artifact, part, outputs_dir, declared_inline, inline, facts)


def _land_text(
    artifact: A2aArtifact,
    part: A2aPart,
    outputs_dir: Path,
    declared_inline: dict[str, Any],
    inline: dict[str, Any],
) -> None:
    if artifact.name in declared_inline:
        inline[artifact.name] = part.text
        return
    (outputs_dir / safe_filename(artifact.name)).write_text(part.text, encoding="utf-8")


def _land_data(
    artifact: A2aArtifact,
    part: A2aPart,
    outputs_dir: Path,
    declared_inline: dict[str, Any],
    inline: dict[str, Any],
    facts: dict[str, str | int | float | bool],
) -> None:
    if artifact.name in declared_inline:
        inline[artifact.name] = part.data
    elif artifact.name == "facts" and isinstance(part.data, dict):
        for key, value in part.data.items():
            if isinstance(value, str | int | float | bool):
                facts[str(key)] = value
    else:
        name = safe_filename(artifact.name)
        (outputs_dir / (name if name.endswith(".json") else f"{name}.json")).write_text(
            json.dumps(part.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
