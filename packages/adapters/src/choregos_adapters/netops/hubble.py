# SPDX-License-Identifier: Apache-2.0
"""Hubble flows, read through the `hubble` CLI against Hubble Relay (ADR 0052).

Why the CLI and not a gRPC client: Relay speaks gRPC only (`observer.Observer/GetFlows`). Talking
to it from Python means `grpcio` (a native wheel the platform does not carry today) plus the
generated code of `observer.proto` and `flow.proto` — several thousand lines to keep in step with
Cilium. `hubble observe --output jsonpb` is the same call, maintained by the Cilium project, and
its output is one JSON object per flow: that is the format the connector parses, the format the
fake replays (`fakes/recordings/*.jsonl`), and the format an operator records with
`hubble observe -o jsonpb > trace.jsonl`. The binary is pinned in the API/worker image, where the
connector runs (`docker/api.Dockerfile`), never in the runner: the agent's pod has no route and no
credential to Relay.

A flow is NORMALISED before anyone reads it: who talks to whom, on which port, the verdict, and —
for a drop — the reason and the policies that denied it. The raw flow carries MAC addresses, node
IDs and trace contexts an agent has no use for.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..errors import AdapterError, UpstreamError

VERDICTS = frozenset({"FORWARDED", "DROPPED", "ERROR", "AUDIT", "REDIRECTED", "TRACED", "TRANSLATED"})


def _get(source: dict[str, Any], *names: str) -> Any:
    """jsonpb keeps the proto names (`drop_reason_desc`, `IP`, `TCP`); camelCase is accepted too."""
    for name in names:
        if name in source:
            return source[name]
    return None


def _endpoint(raw: dict[str, Any] | None, ip: str | None, port: int | None) -> dict[str, Any]:
    raw = raw or {}
    labels = [str(label) for label in raw.get("labels") or []]
    return {
        "namespace": raw.get("namespace") or None,
        "pod": _get(raw, "pod_name", "podName") or None,
        "identity": raw.get("identity"),
        "labels": labels,
        "ip": ip,
        "port": port,
    }


def _policy_names(policies: Iterable[Any] | None) -> list[str]:
    names = []
    for policy in policies or []:
        if isinstance(policy, dict):
            namespace, name = policy.get("namespace"), policy.get("name")
            kind = policy.get("kind") or "CiliumNetworkPolicy"
            names.append(f"{kind}/{namespace}/{name}" if namespace else f"{kind}/{name}")
    return names


def normalize_flow(line: dict[str, Any]) -> dict[str, Any]:
    """One line of `hubble observe -o jsonpb` (or a bare flow) → what an agent reads."""
    flow = line.get("flow", line)
    ip = _get(flow, "IP", "ip") or {}
    l4 = _get(flow, "l4", "L4") or {}
    protocol, transport = next(
        ((name, value) for name, value in l4.items() if isinstance(value, dict)), ("", {})
    )
    source_port = _get(transport, "source_port", "sourcePort")
    destination_port = _get(transport, "destination_port", "destinationPort")
    verdict = str(flow.get("verdict") or "VERDICT_UNKNOWN")
    normalized: dict[str, Any] = {
        "time": flow.get("time") or line.get("time"),
        "verdict": verdict,
        "direction": _get(flow, "traffic_direction", "trafficDirection"),
        "protocol": protocol or None,
        "source": _endpoint(flow.get("source"), ip.get("source"), source_port),
        "destination": _endpoint(flow.get("destination"), ip.get("destination"), destination_port),
        "summary": _get(flow, "Summary", "summary"),
        "node": _get(flow, "node_name", "nodeName") or line.get("node_name"),
    }
    if verdict == "DROPPED":
        normalized["drop_reason"] = _get(flow, "drop_reason_desc", "dropReasonDesc") or str(
            _get(flow, "drop_reason", "dropReason") or "UNKNOWN"
        )
        normalized["denied_by"] = _policy_names(
            [
                *(_get(flow, "ingress_denied_by", "ingressDeniedBy") or []),
                *(_get(flow, "egress_denied_by", "egressDeniedBy") or []),
            ]
        )
    return normalized


@dataclass(frozen=True, slots=True)
class FlowFilter:
    """What `get_flows` accepts — the subset of `hubble observe` flags an investigation needs."""

    namespace: str | None = None
    pod: str | None = None
    verdict: str | None = None
    since: str | None = None
    last: int = 100

    def __post_init__(self) -> None:
        if self.verdict is not None and self.verdict not in VERDICTS:
            raise AdapterError(f"unknown verdict {self.verdict} (known: {', '.join(sorted(VERDICTS))})")
        if not 1 <= self.last <= 2000:
            raise AdapterError("`last` is between 1 and 2000")

    def matches(self, flow: dict[str, Any]) -> bool:
        """The CLI's semantics, for the recorded trace: a namespace or a pod on EITHER side."""
        ends = (flow["source"], flow["destination"])
        if self.namespace and not any(end["namespace"] == self.namespace for end in ends):
            return False
        if self.pod and not any(str(end["pod"] or "").startswith(self.pod) for end in ends):
            return False
        return not (self.verdict and flow["verdict"] != self.verdict)


class FlowSource(Protocol):
    async def flows(self, selection: FlowFilter) -> list[dict[str, Any]]: ...


def parse_jsonpb(text: str) -> list[dict[str, Any]]:
    """One JSON object per line; blank lines ignored, a malformed one named."""
    lines = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError as error:
            raise AdapterError(f"hubble: line {number} is not JSON: {error.msg}") from error
    return lines


@dataclass
class RecordedFlows:
    """A recorded trace replayed as if Relay answered it — the fake, and the eval's fixture."""

    lines: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path) -> RecordedFlows:
        return cls(parse_jsonpb(path.read_text(encoding="utf-8")))

    async def flows(self, selection: FlowFilter) -> list[dict[str, Any]]:
        normalized = [normalize_flow(line) for line in self.lines if "flow" in line or "verdict" in line]
        return [flow for flow in normalized if selection.matches(flow)][-selection.last :]


@dataclass
class HubbleCli:
    """`hubble observe` against Relay. The client key, when Relay asks for mTLS, is written to a
    0600 temporary file for the duration of the call and removed after it."""

    server: str
    binary: str = "hubble"
    tls: bool = True
    ca_cert: str = ""
    server_name: str = ""
    client_cert: str = ""
    client_key: str = field(default="", repr=False)
    timeout_s: float = 30.0

    def command(self, selection: FlowFilter, files: dict[str, str]) -> list[str]:
        command = [self.binary, "observe", "--server", self.server, "--output", "jsonpb"]
        command += ["--last", str(selection.last)]
        if selection.namespace:
            command += ["--namespace", selection.namespace]
        if selection.pod:
            command += ["--pod", selection.pod]
        if selection.verdict:
            command += ["--verdict", selection.verdict]
        if selection.since:
            command += ["--since", selection.since]
        if self.tls:
            command.append("--tls")
            if "ca" in files:
                command += ["--tls-ca-cert-files", files["ca"]]
            if self.server_name:
                command += ["--tls-server-name", self.server_name]
            if "cert" in files and "key" in files:
                command += ["--tls-client-cert-file", files["cert"], "--tls-client-key-file", files["key"]]
        return command

    async def flows(self, selection: FlowFilter) -> list[dict[str, Any]]:
        with tempfile.TemporaryDirectory(prefix="choregos-hubble-") as directory:
            files: dict[str, str] = {}
            for name, pem in (("ca", self.ca_cert), ("cert", self.client_cert), ("key", self.client_key)):
                if pem:
                    path = Path(directory) / f"{name}.pem"
                    path.write_text(pem, encoding="utf-8")
                    path.chmod(0o600)
                    files[name] = str(path)
            try:
                process = await asyncio.create_subprocess_exec(
                    *self.command(selection, files),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")},
                )
            except FileNotFoundError as error:
                raise UpstreamError(
                    "hubble", f"`{self.binary}` is not installed where this connector runs"
                ) from error
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_s)
            except TimeoutError as error:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                raise UpstreamError(
                    "hubble", f"no answer from {self.server} in {self.timeout_s:.0f} s"
                ) from error
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()[:500]
            raise UpstreamError("hubble", f"hubble observe failed ({process.returncode}): {detail}")
        return [normalize_flow(line) for line in parse_jsonpb(stdout.decode(errors="replace"))]
