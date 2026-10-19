# SPDX-License-Identifier: Apache-2.0
"""Executor Docker local : pour développer sans Kubernetes (`dev/compose.yaml`)."""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
from collections.abc import AsyncIterator
from typing import Any

from choregos_contracts import ExecutorKind, StageResult
from choregos_core.domain import RUN_GATEWAY_KEY_ENV, RUN_REMOTE_TOKEN_ENV, ExecRef, ExecStatus, StageJobSpec

from ..errors import UpstreamError

RESULT_PATH = "/workspace/.choregos/result.json"
PULL_TIMEOUT_S = 900.0
#: How many finished run containers are kept for `docker logs` and the result (#439). The older
#: ones are removed when a new run starts: by then the orchestrator has long read them.
KEEP_FINISHED = 10


class LocalDockerExecutor:
    """Lance le runner dans un conteneur local, avec les mêmes variables qu'en cluster."""

    kind = ExecutorKind.LOCAL_DOCKER
    capabilities: frozenset[str] = frozenset()

    def __init__(self, *, network: str = "choregos_default", docker: str = "docker") -> None:
        self.network = network
        self.docker = docker
        self.containers: dict[str, str] = {}

    def _name(self, run_id: str) -> str:
        return f"choregos-run-{run_id}"[:60].lower()

    async def _run_raw(
        self, *args: str, timeout: float = 120.0, env: dict[str, str] | None = None
    ) -> tuple[int, bytes, str]:
        process = await asyncio.create_subprocess_exec(
            self.docker,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **env} if env else None,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return process.returncode or 0, stdout, stderr.decode(errors="replace")

    async def _run(
        self, *args: str, timeout: float = 120.0, env: dict[str, str] | None = None
    ) -> tuple[int, str, str]:
        code, stdout, stderr = await self._run_raw(*args, timeout=timeout, env=env)
        return code, stdout.decode(errors="replace"), stderr

    async def _prune_finished(self) -> None:
        """Remove the finished run containers beyond the newest `KEEP_FINISHED` (#439).

        Each one holds its run's workspace; without this, every run left a container behind.
        `docker ps` lists the newest first. A failure here never stops a run from starting.
        """
        filters = ("--filter", "label=choregos.run-id", "--filter", "status=exited")
        code, stdout, _ = await self._run("ps", "--all", *filters, "--format", "{{.Names}}")
        stale = stdout.split()[KEEP_FINISHED:] if code == 0 else []
        if stale:
            await self._run("rm", "--force", *stale)

    async def start(self, spec: StageJobSpec) -> ExecRef:
        name = self._name(spec.run_id)
        code, _, _ = await self._run("inspect", name)
        if code == 0:
            return ExecRef(kind=self.kind, name=name, run_id=spec.run_id)
        await self._prune_finished()
        secrets = {"CHOREGOS_RUN_TOKEN": spec.run_token}
        # The run's gateway key next to its token (#523), never read from the run's input.
        if spec.gateway_key:
            secrets[RUN_GATEWAY_KEY_ENV] = spec.gateway_key
        if spec.remote_token:
            secrets[RUN_REMOTE_TOKEN_ENV] = spec.remote_token
        args = [
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            self.network,
            # What the Kubernetes Job's security context says (k8s_job.py), as far as Docker can.
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--env",
            f"CHOREGOS_RUN_ID={spec.run_id}",
            "--env",
            f"CHOREGOS_API_URL={spec.api_url}",
            # The secrets are named here and their values travel in docker's own environment
            # (#439): a value on the command line is readable by anyone on the host (`ps`).
            *(arg for key in secrets for arg in ("--env", key)),
            "--label",
            f"choregos.run-id={spec.run_id}",
            "--label",
            f"choregos.project={spec.project_slug}",
        ]
        for key, value in spec.env.items():
            args += ["--env", f"{key}={value}"]
        args += [spec.runner_image, "run"]
        # The first run pulls the runner image (more than a gigabyte): two minutes are not enough,
        # and a `docker run` cut short keeps pulling, then collides with the retry on the name.
        code, _stdout, stderr = await self._run(*args, timeout=PULL_TIMEOUT_S, env=secrets)
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
            reason="OOMKilled" if state.get("OOMKilled") else None,
            message=str(state.get("Error", ""))[:300],
        )

    async def fetch_result(self, ref: ExecRef) -> StageResult | None:
        """The result file the runner left in its workspace, read from the STOPPED container.

        `docker exec` (the first version) needs a running container, and this is only called once
        the run has ended: it always failed. `docker cp … -` reads a stopped one, as a tar stream.
        """
        code, stdout, _ = await self._run_raw("cp", f"{ref.name}:{RESULT_PATH}", "-")
        if code != 0:
            return None
        try:
            with tarfile.open(fileobj=io.BytesIO(stdout)) as archive:
                member = archive.next()
                extracted = archive.extractfile(member) if member is not None else None
                if extracted is None:
                    return None
                return StageResult.model_validate(json.loads(extracted.read()))
        except (tarfile.TarError, json.JSONDecodeError, ValueError):
            return None

    async def logs(self, ref: ExecRef) -> AsyncIterator[str]:
        _, stdout, stderr = await self._run("logs", ref.name)
        for line in (stdout + stderr).splitlines():
            yield line

    async def cancel(self, ref: ExecRef) -> None:
        await self._run("rm", "-f", ref.name)
