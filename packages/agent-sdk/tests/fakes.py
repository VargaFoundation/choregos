"""A stand-in for the runner's side of an ACP connection, for tests that need no subprocess."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse
from choregos_agent_sdk.session import RunSession


class FakeConn:
    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.updates: list[Any] = []
        self.asked: list[str] = []

    async def session_update(self, session_id: str, update: Any) -> None:
        self.updates.append(update)

    async def request_permission(self, session_id: str, tool_call: Any, options: Any) -> Any:
        self.asked.append(str(tool_call.title))
        if self.allow:
            return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id="allow"))
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


def make_session(tmp_path: Path, conn: FakeConn | None = None, max_turns: int | None = None) -> RunSession:
    return RunSession(conn=conn or FakeConn(), session_id="s1", workspace=tmp_path, max_turns=max_turns)
