"""The LLM guard in a real LiteLLM, as the chart ships it: request and answer masked (S40-21b).

The unit tests drive the guard with stand-ins for LiteLLM's objects; only LiteLLM itself proves
that the hooks fire, on which routes, with which shapes. This test renders the chart, runs its
LiteLLM image with the chart's very `config.yaml` and guard file, in front of a fake provider
that answers with secrets (`llm_guard_upstream.py`), and calls it with a key minted marked, as
the platform mints a run's key under `sandbox.llm_security_analyzer`, and with one unmarked.

Docker and Helm, and an opt-in — it pulls images and takes a minute:

    CHOREGOS_LIVE_LLM_GUARD=1 uv run pytest tests/live/test_llm_guard_live.py -m live
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx
import pytest
import yaml
from choregos_core.llm_guardrail import GUARD_METADATA_KEY, GUARD_REDACT, REDACTED

from .conftest import require
from .llm_guard_upstream import ARGUMENTS, PASSWORD, SECRET

pytestmark = pytest.mark.live

ROOT = pathlib.Path(__file__).resolve().parents[2]
UPSTREAM = pathlib.Path(__file__).with_name("llm_guard_upstream.py")
MASTER = "sk-master-" + uuid4().hex
MODEL = "platform/standard"


@dataclass
class Gateway:
    url: str
    upstream: str

    def key(self, metadata: dict[str, Any]) -> str:
        minted = httpx.post(
            f"{self.url}/key/generate",
            headers={"Authorization": f"Bearer {MASTER}"},
            json={"metadata": metadata, "models": [MODEL]},
            timeout=30,
        )
        minted.raise_for_status()
        return str(minted.json()["key"])

    def call(self, key: str, route: str, stream: bool, content: str = "hi") -> str:
        body: dict[str, Any] = {
            "model": MODEL,
            "stream": stream,
            "messages": [{"role": "user", "content": content}],
        }
        if route == "/v1/messages":
            body["max_tokens"] = 50
        answer = httpx.post(
            f"{self.url}{route}", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60
        )
        assert answer.status_code == 200, answer.text
        return answer.text

    def seen(self) -> list[dict[str, Any]]:
        seen: list[dict[str, Any]] = httpx.get(f"{self.upstream}/_seen", timeout=10).json()
        return seen


def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout.strip()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _chart_gateway(into: pathlib.Path) -> str:
    """The chart's LiteLLM files written to `into`; returns its image."""
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "choregos",
            str(ROOT / "charts" / "choregos"),
            "--set",
            "global.gateway.embedded=true",
            "--set",
            "choregos-litellm.upstream.apiBase=http://upstream:8080/v1",
            "--set",
            "choregos-litellm.upstream.apiKeySecret=upstream",
            "--api-versions",
            "extensions.agents.x-k8s.io/v1beta1/SandboxTemplate",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    docs = [
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("metadata", {}).get("name") == "choregos-litellm"
    ]
    (config,) = [doc for doc in docs if doc["kind"] == "ConfigMap"]
    (deployment,) = [doc for doc in docs if doc["kind"] == "Deployment"]
    for name, text in config["data"].items():
        (into / name).write_text(text, encoding="utf-8")
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    return str(container["image"])


def _wait(url: str, seconds: int = 240) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise TimeoutError(url)


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Gateway]:
    require("CHOREGOS_LIVE_LLM_GUARD")
    for tool in ("docker", "helm"):
        if shutil.which(tool) is None:
            pytest.skip(f"live test skipped — `{tool}` absent")
    files = tmp_path_factory.mktemp("litellm")
    files.chmod(0o755)
    image = _chart_gateway(files)
    shutil.copy(UPSTREAM, files / UPSTREAM.name)
    for path in files.iterdir():
        path.chmod(0o644)
    name = f"choregos-llm-guard-{uuid4().hex[:8]}"
    gateway_port, upstream_port = _free_port(), _free_port()
    user = f"{os.getuid()}:{os.getgid()}"
    try:
        _docker("network", "create", name)
        _docker(
            "run", "-d", "--name", f"{name}-pg", "--network", name,
            "-e", "POSTGRES_PASSWORD=pw", "-e", "POSTGRES_DB=litellm", "postgres:16-alpine",
        )  # fmt: skip
        _docker(
            "run", "-d", "--name", f"{name}-upstream", "--network", name, "--network-alias", "upstream",
            "-p", f"127.0.0.1:{upstream_port}:8080", "-v", f"{files}:/files:ro",
            "python:3.12-slim", "python", f"/files/{UPSTREAM.name}", "8080",
        )  # fmt: skip
        _docker(
            "run", "-d", "--name", f"{name}-litellm", "--network", name, "--user", user,
            "-p", f"127.0.0.1:{gateway_port}:4000", "-v", f"{files}:/etc/litellm:ro",
            "-e", f"LITELLM_MASTER_KEY={MASTER}", "-e", "UPSTREAM_API_KEY=upstream",
            "-e", f"DATABASE_URL=postgresql://postgres:pw@{name}-pg:5432/litellm",
            image, "--config", "/etc/litellm/config.yaml", "--port", "4000",
        )  # fmt: skip
        url = f"http://127.0.0.1:{gateway_port}"
        _wait(f"{url}/health/liveliness")
        yield Gateway(url=url, upstream=f"http://127.0.0.1:{upstream_port}")
    finally:
        for container in ("litellm", "upstream", "pg"):
            subprocess.run(["docker", "rm", "-f", f"{name}-{container}"], capture_output=True, check=False)
        subprocess.run(["docker", "network", "rm", name], capture_output=True, check=False)


ROUTES = [
    ("/v1/chat/completions", False),
    ("/v1/chat/completions", True),
    ("/v1/messages", False),
    ("/v1/messages", True),
]


def _events(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[5:])
        for line in text.splitlines()
        if line.startswith("data:") and line.strip() != "data: [DONE]"
    ]


def _read(text: str) -> tuple[str, str]:
    """The answer's text and its tool call's arguments, put back together from whichever shape
    the route answered in: a stream cuts every secret across chunks."""
    if not text.startswith(("data:", "event:")):
        answer = json.loads(text)
        if "choices" in answer:
            message = answer["choices"][0]["message"]
            return str(message["content"]), str(message["tool_calls"][0]["function"]["arguments"])
        blocks = answer["content"]
        (tool,) = [block for block in blocks if block["type"] == "tool_use"]
        words = "".join(block["text"] for block in blocks if block["type"] == "text")
        return words, json.dumps(tool["input"])
    words, arguments = [], []
    for event in _events(text):
        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            words.append(delta.get("content") or "")
            for call in delta.get("tool_calls") or []:
                arguments.append(call.get("function", {}).get("arguments") or "")
        if event.get("type") == "content_block_delta":
            words.append(event["delta"].get("text", ""))
            arguments.append(event["delta"].get("partial_json", ""))
    return "".join(words), "".join(arguments)


@pytest.mark.parametrize(("route", "stream"), ROUTES)
def test_a_marked_key_never_sees_a_secret_in_the_model_s_answer(
    gateway: Gateway, route: str, stream: bool
) -> None:
    answer = gateway.call(gateway.key({GUARD_METADATA_KEY: GUARD_REDACT}), route, stream)
    words, arguments = _read(answer)
    for seen in (answer, words):
        assert SECRET not in seen and PASSWORD not in seen
    assert REDACTED in words
    assert json.loads(arguments) == {"path": "k.txt", "content": REDACTED}


@pytest.mark.parametrize(("route", "stream"), ROUTES)
def test_an_unmarked_key_gets_the_answer_as_the_provider_wrote_it(
    gateway: Gateway, route: str, stream: bool
) -> None:
    """The proof that the fake provider does answer with secrets: the test above is not vacuous."""
    words, arguments = _read(gateway.call(gateway.key({"run_id": "r1"}), route, stream))
    assert SECRET in words and PASSWORD in words and REDACTED not in words
    assert json.loads(arguments) == json.loads(ARGUMENTS)


def test_a_marked_key_s_request_reaches_the_provider_masked(gateway: Gateway) -> None:
    httpx.delete(f"{gateway.upstream}/_seen", timeout=10)
    content = f"deploy with {SECRET} and clone https://bot:{PASSWORD}@git.example/a.git"
    gateway.call(gateway.key({GUARD_METADATA_KEY: GUARD_REDACT}), "/v1/chat/completions", False, content)
    gateway.call(gateway.key({}), "/v1/chat/completions", False, content)
    marked, unmarked = (json.dumps(seen["body"]["messages"]) for seen in gateway.seen())
    assert SECRET not in marked and PASSWORD not in marked and REDACTED in marked
    assert SECRET in unmarked
