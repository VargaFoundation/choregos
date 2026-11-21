# SPDX-License-Identifier: Apache-2.0
"""The CLI's HTTP client: an API token or a session cookie, readable errors."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

#: The largest page the API serves (ADR 0206): a list read to its end asks for this many rows at a time.
PAGE_LIMIT = 200
CONFIG_PATH = Path(os.environ.get("TRANIBOR_CONFIG", Path.home() / ".config" / "choregos" / "config.json"))


class NoOrganisationError(RuntimeError):
    """Neither `--org`, the profile nor `TRANIBOR_ORG` names an organisation."""


@dataclass
class Profile:
    """The connection profile, stored in `~/.config/choregos/config.json`."""

    api_url: str = "http://localhost:8000"
    token: str = ""
    #: No default organisation: a fresh installation names its own (`global.bootstrap.org`), and a
    #: hard-coded one pointed every newcomer at an organisation that does not exist.
    org: str = ""

    @classmethod
    def load(cls) -> Profile:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in {"api_url", "token", "org"}})
        return cls(
            api_url=os.environ.get("TRANIBOR_API_URL", "http://localhost:8000"),
            token=os.environ.get("TRANIBOR_TOKEN", ""),
            org=os.environ.get("TRANIBOR_ORG", ""),
        )

    def organisation(self, override: str | None = None) -> str:
        """`--org`, else the profile's organisation; otherwise a usage error naming both ways."""
        chosen = override or self.org or os.environ.get("TRANIBOR_ORG", "")
        if not chosen:
            raise NoOrganisationError(
                "no organisation: pass --org <slug>, set TRANIBOR_ORG, or save one in the profile "
                "with `choregos login --org <slug>` (`choregos orgs list` shows yours)"
            )
        return chosen

    def save(self) -> Path:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps({"api_url": self.api_url, "token": self.token, "org": self.org}, indent=2),
            encoding="utf-8",
        )
        CONFIG_PATH.chmod(0o600)
        return CONFIG_PATH


def _event_data(raw: str) -> dict[str, Any]:
    """An event's data: a JSON object, or the text itself under `text` when it is not one."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"text": raw}
    return value if isinstance(value, dict) else {"value": value}


class ApiError(RuntimeError):
    def __init__(self, status_code: int, problem: dict[str, Any] | str) -> None:
        #: The problem's machine `code`, when the API gave one: what a command tells refusals apart by.
        self.code: str | None = None
        if isinstance(problem, dict):
            code = problem.get("code")
            self.code = code if isinstance(code, str) else None
            detail = problem.get("detail") or problem.get("title") or str(problem)
            errors = problem.get("errors") or []
            if self.code is None and any(
                isinstance(e, dict) and e.get("error") == "step_up_required" for e in errors
            ):
                # The authentication is too old for a decision (a 401 whose `errors` carry the marker).
                self.code = "step_up_required"
            listed = [e for e in errors if isinstance(e, dict) and "msg" in e]
            if listed:
                detail += "\n" + "\n".join(
                    f"  - {'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg', '')}" for e in listed
                )
        else:
            detail = str(problem)[:500]
        super().__init__(f"{status_code} — {detail}")
        self.status_code = status_code


class Client:
    """Calls to the Tranibor API. Every CLI command goes through here."""

    def __init__(self, profile: Profile | None = None) -> None:
        self.profile = profile or Profile.load()
        headers = {"Accept": "application/json"}
        if self.profile.token:
            headers["Authorization"] = f"Bearer {self.profile.token}"
        self._client = httpx.Client(
            base_url=self.profile.api_url.rstrip("/") + "/api/v1", headers=headers, timeout=30.0
        )

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        kwargs.pop("quiet", None)  # a refusal is raised here whatever it says; `_ReadableClient` reads it
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                raise ApiError(response.status_code, response.json())
            except (json.JSONDecodeError, ValueError):
                raise ApiError(response.status_code, response.text) from None
        self._check_version()
        return response.json() if response.content else None

    def _check_version(self) -> None:
        """Once per process, at most once a day: one stderr line when the instance runs another version."""
        from .self_update import CHECK_TIMEOUT_S, check_version

        def edition() -> Any:
            return self._client.get("/edition", timeout=CHECK_TIMEOUT_S).json()

        check_version(self.profile.api_url, edition)

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def get_pages(self, path: str, **kwargs: Any) -> list[Any]:
        """Every row of a paged list (ADR 0206): `GET path` page after page, following `meta.next_cursor` to
        the end. A cursor that comes back unchanged stops the loop rather than spinning on it."""
        params = dict(kwargs.pop("params", None) or {})
        params.setdefault("limit", PAGE_LIMIT)
        rows: list[Any] = []
        while True:
            page = self.get(path, params=params, **kwargs)
            if isinstance(page, list):  # an API older than ADR 0206 returns the bare array
                return rows + page
            rows += page["items"]
            meta = page.get("meta") or {}
            cursor = meta.get("next_cursor")
            if not meta.get("has_more") or not cursor or cursor == params.get("cursor"):
                return rows
            params["cursor"] = cursor

    def get_raw(self, path: str, **kwargs: Any) -> httpx.Response:
        """A GET whose answer is bytes, not JSON (an artefact's content): the response as served."""
        response = self._client.get(path, **kwargs)
        if response.status_code >= 400:
            try:
                raise ApiError(response.status_code, response.json())
            except (json.JSONDecodeError, ValueError):
                raise ApiError(response.status_code, response.text) from None
        self._check_version()
        return response

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def stream_events(self, method: str, path: str, **kwargs: Any) -> Iterator[tuple[str, dict[str, Any]]]:
        """Server-sent events as `(event, data)`, in the order they come: a conversation's turn (`POST`), the
        organisation's activity (`GET`). An event without a name is `message`, as the SSE format says; a
        refusal before the stream opens is an `ApiError`, as for any other call."""
        headers = {"Accept": "text/event-stream"}
        with self._client.stream(method, path, headers=headers, timeout=None, **kwargs) as response:
            if response.status_code >= 400:
                response.read()
                try:
                    raise ApiError(response.status_code, response.json())
                except (json.JSONDecodeError, ValueError):
                    raise ApiError(response.status_code, response.text) from None
            name, data = "message", list[str]()
            for line in response.iter_lines():
                if not line:
                    if data:
                        yield name, _event_data("\n".join(data))
                    name, data = "message", list[str]()
                elif line.startswith("event:"):
                    name = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data.append(line.removeprefix("data:").strip())
            if data:
                yield name, _event_data("\n".join(data))

    def stream_sse(self, path: str, **kwargs: Any) -> Any:
        """Follow an SSE stream (a run's journal, provisioning)."""
        with self._client.stream(
            "GET", path, headers={"Accept": "text/event-stream"}, timeout=None, **kwargs
        ) as response:
            for line in response.iter_lines():
                if line.startswith("data:"):
                    payload = line.removeprefix("data:").strip()
                    if payload:
                        yield json.loads(payload)


class AsyncClient:
    """The same calls for the workspace (`choregos chat`), which lives in an event loop: a turn is read as a
    task, so ending the task closes the stream and the platform cancels the turn (S63-04b)."""

    def __init__(self, profile: Profile | None = None) -> None:
        self.profile = profile or Profile.load()
        headers = {"Accept": "application/json"}
        if self.profile.token:
            headers["Authorization"] = f"Bearer {self.profile.token}"
        self._client = httpx.AsyncClient(
            base_url=self.profile.api_url.rstrip("/") + "/api/v1", headers=headers, timeout=30.0
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise _problem(response)
        return response.json() if response.content else None

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def get_pages(self, path: str, **kwargs: Any) -> list[Any]:
        """`Client.get_pages`, awaited: every row of a paged list, following `meta.next_cursor` to the end."""
        params = dict(kwargs.pop("params", None) or {})
        params.setdefault("limit", PAGE_LIMIT)
        rows: list[Any] = []
        while True:
            page = await self.get(path, params=params, **kwargs)
            if isinstance(page, list):  # an API older than ADR 0206 returns the bare array
                return rows + page
            rows += page["items"]
            meta = page.get("meta") or {}
            cursor = meta.get("next_cursor")
            if not meta.get("has_more") or not cursor or cursor == params.get("cursor"):
                return rows
            params["cursor"] = cursor

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def stream_events(
        self, method: str, path: str, **kwargs: Any
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """`Client.stream_events`, awaited: `(event, data)` in the order they come."""
        headers = {"Accept": "text/event-stream"}
        async with self._client.stream(method, path, headers=headers, timeout=None, **kwargs) as response:
            if response.status_code >= 400:
                await response.aread()
                raise _problem(response)
            name, data = "message", list[str]()
            async for line in response.aiter_lines():
                if not line:
                    if data:
                        yield name, _event_data("\n".join(data))
                    name, data = "message", list[str]()
                elif line.startswith("event:"):
                    name = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data.append(line.removeprefix("data:").strip())
            if data:
                yield name, _event_data("\n".join(data))


def _problem(response: httpx.Response) -> ApiError:
    try:
        return ApiError(response.status_code, response.json())
    except (json.JSONDecodeError, ValueError):
        return ApiError(response.status_code, response.text)
