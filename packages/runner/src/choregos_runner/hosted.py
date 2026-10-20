# SPDX-License-Identifier: Apache-2.0
"""A step a hosted agent carries (ADR 0080): the agent's own container, beside the runner.

The container serves Foundry's hosted-agent contract on `$PORT`: `GET /readiness`, then `POST
/responses` (the Responses API, streamed as server-sent events) or `POST /invocations` (a JSON body
in, a JSON body out). The runner waits for it to be ready, sends it the step, and reads the answer:
a JSON object with a `status` is the step's result, validated like an agent's `result.json`; any
other text is the summary of a step that declares no output, and a refusal otherwise.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from choregos_contracts import StageInput, StageResult
from choregos_core.hosted import LOOPBACK_HOST

from .outputs import expected_files
from .result import load_result
from .workspace import inline_outputs

Record = Callable[[str, dict[str, Any]], Awaitable[None]]
#: The keys of `artifacts.reports` a hosted step fills.
REPORT_HOSTED_IMAGE = "hosted_image"
REPORT_HOSTED_PROVENANCE = "hosted_provenance"
REPORT_RESPONSE = "hosted_response_id"
#: What the journal keeps of the container's answer.
_EXCERPT = 2000


@dataclass
class HostedStep:
    """How the container answered: `answered` (with its text), `not_ready`, `failed` or `timeout`."""

    status: Literal["answered", "not_ready", "failed", "timeout"]
    text: str = ""
    error: str | None = None
    response_id: str | None = None


def base_url(stage_input: StageInput) -> str:
    assert stage_input.hosted is not None
    return f"http://{LOOPBACK_HOST}:{stage_input.hosted.port}"


async def wait_ready(http: httpx.AsyncClient, base: str, timeout_s: float, interval: float = 1.0) -> bool:
    """Polls `GET /readiness` until it answers 200 or `timeout_s` has passed."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if (await http.get(f"{base}/readiness", timeout=5)).status_code == 200:
                return True
        except httpx.HTTPError:
            pass  # not listening yet
        if time.monotonic() + interval > deadline:
            return False
        await asyncio.sleep(interval)


async def call_responses(
    http: httpx.AsyncClient, base: str, stage_input: StageInput, task: str
) -> HostedStep:
    body = {"input": task, "stream": True, "metadata": _metadata(stage_input)}
    text: list[str] = []
    final: dict[str, Any] | None = None
    async with http.stream(
        "POST", f"{base}/responses", json=body, headers={"accept": "text/event-stream"}
    ) as r:
        if r.status_code != 200:
            detail = (await r.aread()).decode(errors="replace")[:500]
            return HostedStep("failed", error=f"`POST /responses` answered {r.status_code}: {detail}")
        if not r.headers.get("content-type", "").startswith("text/event-stream"):
            return _from_response(json.loads(await r.aread() or b"{}"))
        async for event in _events(r):
            kind = str(event.get("type") or "")
            if kind == "response.output_text.delta":
                text.append(str(event.get("delta") or ""))
            elif kind in ("response.completed", "response.failed", "response.incomplete"):
                final = event.get("response") if isinstance(event.get("response"), dict) else {}
                final = {**(final or {}), "status": kind.removeprefix("response.")}
            elif kind == "error":
                return HostedStep("failed", error=str(event.get("message") or event)[:500])
    if final is None:
        return HostedStep("failed", text="".join(text), error="the stream ended without `response.completed`")
    step = _from_response(final)
    if not step.text and text:
        step.text = "".join(text)
    return step


async def call_invocations(
    http: httpx.AsyncClient, base: str, stage_input: StageInput, task: str
) -> HostedStep:
    body = {"input": task, "metadata": _metadata(stage_input)}
    answer = await http.post(f"{base}/invocations", json=body)
    if answer.status_code != 200:
        return HostedStep(
            "failed", error=f"`POST /invocations` answered {answer.status_code}: {answer.text[:500]}"
        )
    try:
        payload = answer.json()
    except ValueError:
        return HostedStep("answered", text=answer.text)
    if isinstance(payload, dict) and "status" not in payload and isinstance(payload.get("output"), str):
        return HostedStep("answered", text=payload["output"])
    return HostedStep("answered", text=json.dumps(payload) if not isinstance(payload, str) else payload)


async def run_hosted(
    stage_input: StageInput,
    task: str,
    record: Record,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    interval: float = 1.0,
) -> HostedStep:
    """Waits for the container, sends it the step and says how it answered."""
    hosted = stage_input.hosted
    assert hosted is not None
    base = base_url(stage_input)
    # `trust_env=False`: the runner's own proxy variables must not send a loopback call out.
    async with httpx.AsyncClient(
        transport=transport, timeout=httpx.Timeout(None, connect=10), trust_env=False
    ) as http:
        await record(
            "hosted.started", {"image": hosted.image, "protocol": hosted.protocol, "port": hosted.port}
        )
        if not await wait_ready(http, base, hosted.readiness_timeout_s, interval):
            return HostedStep(
                "not_ready", error=f"the container was not ready after {hosted.readiness_timeout_s} s"
            )
        await record("hosted.ready", {})
        call = call_responses if hosted.protocol == "responses" else call_invocations
        timeout_s = stage_input.budget.max_minutes * 60
        try:
            step = await asyncio.wait_for(call(http, base, stage_input, task), timeout=timeout_s)
        except TimeoutError:
            step = HostedStep("timeout", error=f"the container did not answer within {timeout_s} s")
        except (httpx.HTTPError, ValueError) as exc:
            step = HostedStep("failed", error=f"the container's answer could not be read: {str(exc)[:300]}")
    await record(
        "hosted.answered",
        {"status": step.status, "text": step.text[:_EXCERPT], "error": (step.error or "")[:500]},
    )
    return step


def result_of(stage_input: StageInput, workspace: Path, text: str) -> tuple[StageResult | None, str | None]:
    """The step's result from the container's text: a JSON result object, validated; or the text as
    the summary when the step declares nothing; `(None, why)` otherwise."""
    files = expected_files(stage_input)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict) or "status" not in payload:
        if inline_outputs(stage_input) or files:
            return None, "the hosted agent answered text, but the step declares outputs: answer a JSON result"
        if not text.strip():
            return None, "the hosted agent answered nothing"
        payload = {"status": "done", "summary": text.strip()[:4000]}
    path = workspace / ".choregos" / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    load = load_result(path, inline_outputs(stage_input), list(files.values()))
    return load.result, load.error


def _metadata(stage_input: StageInput) -> dict[str, str]:
    return {"choregos_run_id": stage_input.run_id, "choregos_work_item": stage_input.work_item.key}


def _from_response(response: dict[str, Any]) -> HostedStep:
    """A Responses API object: its `output_text` parts, or why it failed."""
    texts = [
        str(part.get("text") or "")
        for item in response.get("output") or []
        if isinstance(item, dict)
        for part in item.get("content") or []
        if isinstance(part, dict) and part.get("type") == "output_text"
    ]
    status = response.get("status")
    if status in ("failed", "incomplete", "cancelled"):
        error = response.get("error") or response.get("incomplete_details") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        return HostedStep(
            "failed", text="".join(texts), error=f"the response is {status}: {message or 'no detail'}"
        )
    return HostedStep("answered", text="".join(texts), response_id=response.get("id"))


async def _events(response: httpx.Response) -> Any:
    """The `data:` payloads of a server-sent event stream, as JSON objects."""
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line.strip() and data:
            raw, data = "\n".join(data), []
            if raw == "[DONE]":
                return
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event
    if data:
        try:
            event = json.loads("\n".join(data))
        except json.JSONDecodeError:
            return
        if isinstance(event, dict):
            yield event
