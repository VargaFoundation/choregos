"""Executor Docker local : pour développer sans Kubernetes (`dev/compose.yaml`)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from choregos_contracts import ExecutorKind, StageResult
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec

from ..errors import UpstreamError


class LocalDockerExecutor:
    """Lance le runner dans un conteneur local, avec les mêmes variables qu'en cluster."""

    kind = ExecutorKind.LOCAL_DOCKER

    def __init__(self, *, network: str = "choregos_default", docker: str = "docker") -> None:
        self.network = network
        self.docker = docker
        self.containers: dict[str, str] = {}

    def _name(self, run_id: str) -> str:
        return f"choregos-run-{run_id}"[:60].lower()

    async def _run(self, *args: str, timeout: float = 120.0) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            self.docker, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return process.returncode or 0, stdout.decode(), stderr.decode()

    async def start(self, spec: StageJobSpec) -> ExecRef:
        name = self._name(spec.run_id)
        code, _, _ = await self._run("inspect", name)
        if code == 0:
            return ExecRef(kind=self.kind, name=name, run_id=spec.run_id)
        args = [
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            self.network,
            "--env",
            f"CHOREGOS_RUN_ID={spec.run_id}",
            "--env",
            f"CHOREGOS_API_URL={spec.api_url}",
            "--env",
            f"CHOREGOS_RUN_TOKEN={spec.run_token}",
            "--label",
            f"choregos.run-id={spec.run_id}",
            "--label",
            f"choregos.project={spec.project_slug}",
        ]
        for key, value in spec.env.items():
            args += ["--env", f"{key}={value}"]
        args += [spec.runner_image, "run"]
        code, _stdout, stderr = await self._run(*args)
        if code != 0:
            raise UpstreamError("docker", f"démarrage impossible : {stderr[:300]}")
        self.containers[spec.run_id] = name
        return ExecRef(kind=self.kind, name=name, run_id=spec.run_id)

    async def status(self, ref: ExecRef) -> ExecStatus:
        code, stdout, _ = await self._run("inspect", ref.name)
        if code != 0:
            return ExecStatus(state="unknown", message="conteneur introuvable")
        payload: list[dict[str, Any]] = json.loads(stdout)
        state = payload[0].get("State", {}) if payload else {}
        if state.get("Running"):
            return ExecStatus(state="running")
        exit_code = int(state.get("ExitCode", 0))
        return ExecStatus(
            state="succeeded" if exit_code == 0 else "failed",
            exit_code=exit_code,
            message=str(state.get("Error", ""))[:300],
        )

    async def fetch_result(self, ref: ExecRef) -> StageResult | None:
        code, stdout, _ = await self._run("exec", ref.name, "cat", "/workspace/.choregos/result.json")
        if code != 0:
            return None
        try:
            return StageResult.model_validate(json.loads(stdout))
        except (json.JSONDecodeError, ValueError):
            return None

    async def logs(self, ref: ExecRef) -> AsyncIterator[str]:
        _, stdout, stderr = await self._run("logs", ref.name)
        for line in (stdout + stderr).splitlines():
            yield line

    async def cancel(self, ref: ExecRef) -> None:
        await self._run("rm", "-f", ref.name)
