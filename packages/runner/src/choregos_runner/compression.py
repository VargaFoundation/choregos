# SPDX-License-Identifier: Apache-2.0
"""headroom, started by the runner for one run, on loopback (ADR 0055, S37-07).

WHY
---
headroom compresses what the agent sends the model (tool results, history) on the way to the
gateway. Its own security model says it is not multi-tenant, and it holds plaintext originals so
the model can ask for them back. So it is never shared: the runner starts one process per run,
inside the pod, listening on 127.0.0.1 only, and stops it with the run.

The chain is agent → headroom → gateway. The run's virtual key travels in the agent's requests
and the gateway still meters what is really sent. The proxy itself gets no key, no run token and
no environment from the runner: only what it needs to stay offline.

Every upstream headroom knows points at the gateway. A request in a format we did not wire
would otherwise leave for the provider's public endpoint, carrying the run's key.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from choregos_contracts import ApiFormat, ModelRef, StageInput

#: Every upstream flag of `headroom proxy`, all sent to the gateway.
UPSTREAM_FLAGS = (
    "--anthropic-api-url",
    "--openai-api-url",
    "--gemini-api-url",
    "--cloudcode-api-url",
    "--vertex-api-url",
    "--bedrock-api-url",
)
#: The formats whose clients we point at the proxy. Gemini's CLI is not wired (ADR 0055).
FORMATS = (ApiFormat.ANTHROPIC, ApiFormat.OPENAI)
#: headroom 0.39.1 answers `/livez` in about 6 s in the image, model loaded. `/readyz` probes the
#: gateway too, and answers 503 while the gateway is unreachable: it is not the proxy's state.
READY_TIMEOUT_S = 60.0


class CompressionUnavailableError(RuntimeError):
    """The proxy the run asked for is not there. The policy decides: bypass or fail."""


@dataclass(slots=True)
class Compressor:
    process: asyncio.subprocess.Process
    #: What the agent is given instead of the gateway's URL.
    url: str
    #: Where its output goes, outside the workspace.
    log: Path


def wanted(stage_input: StageInput) -> bool:
    return stage_input.context is not None and stage_input.context.compression == "headroom"


def proxy_home(stage_input: StageInput) -> Path:
    """The proxy's `$HOME` and working directory: outside the workspace and the agent's `$HOME`.
    headroom writes there even with `--stateless` (its logs, onnxruntime's cache)."""
    home = Path(tempfile.gettempdir()) / f"choregos-{stage_input.run_id}-headroom"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _local_url(gateway: str, port: int) -> str:
    """The gateway's URL with the proxy in its place. headroom adds `/v1` to its upstream itself,
    so only a gateway at its root or at `/v1` can be proxied."""
    path = urlparse(gateway).path.rstrip("/")
    if path not in ("", "/v1"):
        raise CompressionUnavailableError(f"the gateway URL has a path headroom cannot forward: `{path}`")
    return f"http://127.0.0.1:{port}{path}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def proxy_env(profile: str, home: Path) -> dict[str, str]:
    """Built from nothing: the runner's environment carries the run token."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "HEADROOM_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "HEADROOM_TELEMETRY": "off",
        "DO_NOT_TRACK": "1",
        "HEADROOM_SAVINGS_PROFILE": profile,
    }
    if "HF_HOME" in os.environ:  # where the image keeps the model fetched at build
        env["HF_HOME"] = os.environ["HF_HOME"]
    return env


def proxy_command(command: str, gateway: str, port: int) -> list[str]:
    upstreams = [part for flag in UPSTREAM_FLAGS for part in (flag, gateway)]
    return [
        *shlex.split(command),
        "proxy",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--stateless",
        "--mode",
        "cache",
        "--no-telemetry",
        *upstreams,
    ]


async def start(
    stage_input: StageInput, *, command: str = "headroom", timeout: float | None = None
) -> Compressor:
    """Starts the run's proxy and waits until it answers. Raises `CompressionUnavailableError`."""
    model = stage_input.model
    profile = stage_input.context.headroom_profile if stage_input.context else "coding"
    if model.api_format not in FORMATS:
        raise CompressionUnavailableError(f"headroom is not wired for the `{model.api_format}` format")
    if not stage_input.gateway_key:
        # Without a run key the agent talks to its provider with its own credentials, and its
        # base URL is left alone: there is no gateway to put the proxy in front of.
        raise CompressionUnavailableError("no gateway key: the agent does not go through the gateway")
    port = _free_port()
    url = _local_url(model.base_url, port)
    home = proxy_home(stage_input)
    log = home / "proxy.log"
    try:
        with log.open("ab") as out:
            process = await asyncio.create_subprocess_exec(
                *proxy_command(command, model.base_url, port),
                cwd=home,
                env=proxy_env(profile, home),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=out,
                stderr=out,
            )
    except OSError as exc:
        raise CompressionUnavailableError(f"headroom did not start: {exc}") from exc
    compressor = Compressor(process, url, log)
    timeout = timeout or READY_TIMEOUT_S
    if await _wait_live(compressor, port, timeout):
        return compressor
    await stop(compressor)
    raise CompressionUnavailableError(f"headroom did not answer within {timeout:.0f} s: {_tail(log)}")


async def _wait_live(compressor: Compressor, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=2.0) as http:
        while time.monotonic() < deadline:
            if compressor.process.returncode is not None:
                return False
            with contextlib.suppress(httpx.HTTPError):
                if (await http.get(f"http://127.0.0.1:{port}/livez")).status_code == 200:
                    return True
            await asyncio.sleep(0.2)
    return False


def _tail(log: Path, size: int = 300) -> str:
    try:
        return log.read_text(encoding="utf-8", errors="replace")[-size:].strip() or "(no output)"
    except OSError:
        return "(no output)"


def through(model: ModelRef, compressor: Compressor) -> ModelRef:
    """The run's model, reached through the proxy. The key is not touched."""
    return model.model_copy(update={"base_url": compressor.url})


async def stop(compressor: Compressor | None) -> None:
    if compressor is None or compressor.process.returncode is not None:
        return
    compressor.process.terminate()
    try:
        await asyncio.wait_for(compressor.process.wait(), timeout=10)
    except TimeoutError:
        compressor.process.kill()
        await compressor.process.wait()
