"""CI en mémoire."""

from __future__ import annotations

import json
from collections.abc import Mapping

from choregos_contracts import InboundEvent, InboundEventType
from choregos_core.domain import CheckRun, CiStatus


class FakeCi:
    def __init__(self) -> None:
        self.statuses: dict[tuple[str, str], CiStatus] = {}
        self.logs_by_ref: dict[str, str] = {}
        self.triggered: list[tuple[str, str, str]] = []

    def set_status(self, repo: str, sha: str, state: str, logs: str = "") -> None:
        self.statuses[(repo, sha)] = CiStatus(
            state=state,
            sha=sha,
            runs=[
                CheckRun(
                    name="ci", status="completed", conclusion="success" if state == "success" else "failure"
                )
            ],
            url=f"https://fake.ci/{repo}/{sha}",
        )
        if logs:
            self.logs_by_ref[f"{repo}@{sha}"] = logs

    async def status_for(self, repo: str, sha: str) -> CiStatus:
        return self.statuses.get((repo, sha), CiStatus(state="unknown", sha=sha))

    async def logs(self, run_ref: str, tail: int = 500) -> str:
        return "\n".join(self.logs_by_ref.get(run_ref, "").splitlines()[-tail:])

    async def trigger(self, repo: str, ref: str, pipeline: str) -> str:
        self.triggered.append((repo, ref, pipeline))
        return f"fake-run-{len(self.triggered)}"

    def parse_event(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        payload = json.loads(body.decode("utf-8"))
        kind = payload.get("state", "succeeded")
        mapping = {
            "succeeded": InboundEventType.CI_SUCCEEDED,
            "failed": InboundEventType.CI_FAILED,
            "started": InboundEventType.CI_STARTED,
        }
        return [
            InboundEvent(
                type=mapping.get(kind, InboundEventType.CI_SUCCEEDED),
                source="fake",
                delivery_id=headers.get("ce-id", "ce-1"),
                payload=payload,
            )
        ]
