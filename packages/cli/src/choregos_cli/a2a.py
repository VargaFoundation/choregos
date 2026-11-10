# SPDX-License-Identifier: Apache-2.0
"""`choregos a2a`: start a workflow over the A2A door, hand it its data and files, follow it to its end.

The door is A2A 0.3.0 JSON-RPC at `/a2a/orgs/<org>` (ADR 0078); `docs/starting-a-workflow-from-outside.md`
says what each call does. This module is the CLI's own small client of that door rather than
`choregos_adapters.a2a.A2aClient`: the CLI depends on `choregos-core` and httpx only, and the adapters'
client parses a task into dataclasses that drop what a script reads here (the raw task for `--json`,
`error.data.errors`, `metadata.choregos`), and has neither the card nor `tasks/resubscribe`.

Exit codes of the commands that wait for the end: 0 completed, 3 failed (or rejected), 4 canceled,
1 when the door refused a call or could not be reached, 130 when interrupted.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
import json
import mimetypes
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .client import NoOrganisationError, Profile

a2a_app = typer.Typer(help="Start and follow a workflow over the A2A door (an `a2a` token)")
console = Console()
errors = Console(stderr=True)

#: The card's extension that gives each skill's fields as a JSON Schema (the door's `SKILL_FIELDS_EXTENSION`).
SKILL_FIELDS_EXTENSION = "https://choregos.dev/a2a/extensions/skill-fields/v1"
#: The states after which a task does not change any more.
FINAL_STATES = frozenset({"completed", "failed", "canceled", "rejected"})
EXIT_COMPLETED, EXIT_REFUSED, EXIT_FAILED, EXIT_CANCELED, EXIT_INTERRUPTED = 0, 1, 3, 4, 130
#: The door pings a stream every 15 seconds: a minute of silence is a dead connection.
STREAM_SILENCE = 60.0
#: The pause before a stream that ended without a final status (the door's hour cut) is opened again.
RECONNECT_PAUSE = 1.0
MISSING_SCOPE = (
    "the A2A door needs a token of scope `a2a`, and refuses a `*` one such as the CLI's profile token: "
    "create one with `choregos tokens create --name <name> --scope a2a --project <org:project>` (as you) "
    "or `choregos machines token <org:project> <application> --name <name>` (as an application), then "
    "pass it with `--token` or CHOREGOS_A2A_TOKEN"
)


class DoorError(RuntimeError):
    """The door answered an HTTP refusal or a JSON-RPC error, or could not be reached."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | None = None,
        data: Any = None,
        missing_scope: bool = False,
        broken: bool = False,
    ) -> None:
        super().__init__(message)
        self.broken = broken
        self.status = status
        self.code = code
        self.data = data
        self.missing_scope = missing_scope

    def explain(self) -> str:
        """The refusal as a person reads it: what the door said, and how to fix a missing scope."""
        where = f"HTTP {self.status}" if self.code is None and self.status else f"JSON-RPC {self.code}"
        lines = [f"{where}: {self}" if self.status or self.code is not None else str(self)]
        if self.missing_scope:
            lines.append(MISSING_SCOPE)
        problems = self.data.get("errors") if isinstance(self.data, dict) else None
        for problem in problems or []:
            if isinstance(problem, dict):
                loc = ".".join(str(p) for p in problem.get("loc") or [])
                lines.append(f"  - {loc or '(message)'}: {problem.get('msg', '')}")
        decision = self.data.get("decision_url") if isinstance(self.data, dict) else None
        if decision:
            lines.append(f"decide in the console: {decision}")
        return "\n".join(lines)


class A2aDoor:
    """An organisation's A2A door. The token is sent, never printed."""

    def __init__(
        self,
        api_url: str,
        org: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.endpoint = f"{api_url.rstrip('/')}/a2a/orgs/{org}"
        headers = {"User-Agent": f"choregos-cli/{__version__}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(transport=transport, headers=headers, timeout=timeout)
        self._ids = itertools.count(1)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> A2aDoor:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    @staticmethod
    def _http_refusal(response: httpx.Response) -> DoorError:
        try:
            body = response.json()
            message = str(body.get("error") if isinstance(body, dict) else body)
        except ValueError:
            message = response.text[:300] or response.reason_phrase
        if response.status_code == 429 and response.headers.get("retry-after"):
            message += f" (retry after {response.headers['retry-after']} s)"
        scope = "insufficient_scope" in response.headers.get("www-authenticate", "")
        return DoorError(message, status=response.status_code, missing_scope=scope)

    @staticmethod
    def _result(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise DoorError("the door did not answer JSON-RPC")
        error = payload.get("error")
        if isinstance(error, dict):
            raise DoorError(
                str(error.get("message") or error), code=error.get("code"), data=error.get("data")
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise DoorError("the door's answer has no result")
        return result

    def _body(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}

    async def card(self) -> dict[str, Any]:
        """The organisation's card: the skills this token may start."""
        try:
            response = await self._http.get(f"{self.endpoint}/.well-known/agent-card.json")
        except httpx.TransportError as exc:
            raise DoorError(f"cannot reach {self.endpoint}: {exc or type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise self._http_refusal(response)
        card = response.json()
        if not isinstance(card, dict):
            raise DoorError("the agent card is not a JSON object")
        return card

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """One JSON-RPC call; its `result`, or a `DoorError`."""
        try:
            response = await self._http.post(self.endpoint, json=self._body(method, params))
        except httpx.TransportError as exc:
            raise DoorError(f"cannot reach {self.endpoint}: {exc or type(exc).__name__}") from exc
        return self._answer(response)

    def _answer(self, response: httpx.Response) -> dict[str, Any]:
        """A JSON-RPC answer, whatever its HTTP status (a parse error is a 400); else an HTTP refusal."""
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("jsonrpc") == "2.0":
            return self._result(payload)
        if response.status_code >= 400:
            raise self._http_refusal(response)
        raise DoorError(f"the door answered HTTP {response.status_code}, not JSON-RPC")

    async def stream(self, method: str, params: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """The `result` of each server-sent event, until the door closes the stream.

        A connection that breaks once the stream has started is a `DoorError` with `broken` set:
        the task is not lost, the caller opens the stream again."""
        timeout = httpx.Timeout(self._http.timeout.connect, read=STREAM_SILENCE)
        started = False
        try:
            async with self._http.stream(
                "POST",
                self.endpoint,
                json=self._body(method, params),
                headers={"Accept": "text/event-stream"},
                timeout=timeout,
            ) as response:
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    # A refusal, before any event: a JSON-RPC error or an HTTP one.
                    await response.aread()
                    yield self._answer(response)
                    return
                started = True
                data: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line.strip() and data:
                        yield self._result(json.loads("\n".join(data)))
                        data = []
                if data:
                    yield self._result(json.loads("\n".join(data)))
        except httpx.TransportError as exc:
            reason = exc or type(exc).__name__
            if started:
                raise DoorError(f"the stream from {self.endpoint} broke: {reason}", broken=True) from exc
            raise DoorError(f"cannot reach {self.endpoint}: {reason}") from exc
        except json.JSONDecodeError as exc:
            raise DoorError("the door's stream carries something that is not JSON") from exc


# ───────────────────────── what the card and a task say ─────────────────────────


def skills_of(card: dict[str, Any]) -> list[dict[str, Any]]:
    """The card's skills, each with `fields`: the JSON Schema of its data part when the card gives one."""
    extensions = (card.get("capabilities") or {}).get("extensions") or []
    fields: dict[str, Any] = {}
    for extension in extensions:
        if isinstance(extension, dict) and extension.get("uri") == SKILL_FIELDS_EXTENSION:
            fields = (extension.get("params") or {}).get("skills") or {}
    return [{**skill, "fields": fields.get(skill.get("id"))} for skill in card.get("skills") or []]


def describe_fields(schema: Any) -> str:
    """`ticket* (string), priority (low|high)`: a required field carries a star."""
    if not isinstance(schema, dict):
        return "—"
    required = set(schema.get("required") or [])
    described = []
    for name, declared in (schema.get("properties") or {}).items():
        spec = declared if isinstance(declared, dict) else {}
        kind = "|".join(str(v) for v in spec["enum"]) if "enum" in spec else str(spec.get("type") or "any")
        described.append(f"{name}{'*' if name in required else ''} ({kind})")
    return ", ".join(described) or "—"


def state_of(result: dict[str, Any]) -> str:
    return str((result.get("status") or {}).get("state") or "unknown")


def is_final(result: dict[str, Any]) -> bool:
    """A status-update that says `final`, or a task (or status) in a state that does not change."""
    if result.get("kind") == "status-update" and result.get("final"):
        return True
    return result.get("kind") in {"task", "status-update"} and state_of(result) in FINAL_STATES


def decision_of(result: dict[str, Any]) -> dict[str, Any] | None:
    """The data part of an input-required status: `decision_url`, `kind`, `request_id`."""
    parts = ((result.get("status") or {}).get("message") or {}).get("parts") or []
    for part in parts:
        data = part.get("data") if isinstance(part, dict) and part.get("kind") == "data" else None
        if isinstance(data, dict) and data.get("decision_url"):
            return data
    return None


def exit_code(task: dict[str, Any]) -> int:
    state = state_of(task)
    if state == "completed":
        return EXIT_COMPLETED
    if state == "canceled":
        return EXIT_CANCELED
    return EXIT_FAILED


# ───────────────────────── the message ─────────────────────────


def parse_fields(pairs: list[str]) -> dict[str, Any]:
    """`key=value`; the value is read as JSON when it is JSON (number, boolean, list), else as text."""
    fields: dict[str, Any] = {}
    for pair in pairs:
        key, equals, value = pair.partition("=")
        if not equals or not key:
            raise ValueError(f"--field {pair!r}: expected `key=value`")
        try:
            fields[key] = json.loads(value)
        except json.JSONDecodeError:
            fields[key] = value
    return fields


def file_part(spec: str) -> dict[str, Any]:
    """`PATH`, or `PATH;type=MEDIA/TYPE` when the extension does not say it (as `curl -F`)."""
    path_text, _, option = spec.partition(";type=")
    path = Path(path_text)
    if not path.is_file():
        raise ValueError(f"--file {spec!r}: no such file")
    media_type = option or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    content = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"kind": "file", "file": {"name": path.name, "mimeType": media_type, "bytes": content}}


def default_message_id(skill: str, title: str | None, parts: list[dict[str, Any]]) -> str:
    """The same command sends the same id: sent again after a timeout, it finds the task it opened.

    It is a digest of what the message carries (skill, title, texts, fields, files), so a script that
    retries does not start the workflow twice; to start it again on purpose, pass `--message-id`."""
    canonical = json.dumps({"skill": skill, "title": title or "", "parts": parts}, sort_keys=True)
    return "cli-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def build_message(
    skill: str,
    *,
    texts: list[str],
    fields: dict[str, Any],
    files: list[dict[str, Any]],
    title: str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """The `message` of `message/send`: text parts are the body, one data part the fields, then the files."""
    parts: list[dict[str, Any]] = [{"kind": "text", "text": text} for text in texts]
    if fields:
        parts.append({"kind": "data", "data": fields})
    parts.extend(files)
    metadata: dict[str, Any] = {"skill": skill}
    if title:
        metadata["title"] = title
    return {
        "kind": "message",
        "role": "user",
        "messageId": message_id or default_message_id(skill, title, parts),
        "metadata": metadata,
        "parts": parts,
    }


# ───────────────────────── following a task ─────────────────────────


async def _pause() -> None:
    await asyncio.sleep(RECONNECT_PAUSE)


async def follow(
    door: A2aDoor,
    task_id: str,
    on_update: Callable[[dict[str, Any]], None],
    *,
    pause: Callable[[], Awaitable[None]] = _pause,
) -> dict[str, Any]:
    """Follow a task to a final state over `tasks/resubscribe`, and return it as `tasks/get` reads it.

    The door ends a stream after an hour, or when the connection dies: a stream that ends without a
    final status is opened again, after `pause`. An input-required status is not final: the person
    decides in the console and the task goes on."""
    while True:
        final = False
        try:
            async for result in door.stream("tasks/resubscribe", {"id": task_id}):
                if result.get("kind") not in {"task", "status-update"}:
                    continue
                on_update(result)
                final = final or is_final(result)
        except DoorError as refused:
            if not refused.broken or final:
                raise
            # A connection that broke, not a refusal: picked up where the task is.
        if final:
            return await door.call("tasks/get", {"id": task_id})
        await pause()


class Printer:
    """One line per change of the task; a decision URL once per request."""

    def __init__(self, out: Console) -> None:
        self.out = out
        self.seen: tuple[str, str, Any] | None = None

    @staticmethod
    def mark(result: dict[str, Any]) -> tuple[str, str, Any]:
        """What makes a status worth a line: the A2A state, the workflow's state, the request."""
        choregos = (result.get("metadata") or {}).get("choregos") or {}
        return (
            state_of(result),
            str(choregos.get("state") or ""),
            (decision_of(result) or {}).get("request_id"),
        )

    def __call__(self, result: dict[str, Any]) -> None:
        choregos = (result.get("metadata") or {}).get("choregos") or {}
        decision = decision_of(result)
        mark = self.mark(result)
        if mark == self.seen:
            return
        self.seen = mark
        where = f" (workflow state `{choregos['state']}`)" if choregos.get("state") else ""
        self.out.print(f"· {mark[0]}{where}", markup=False, highlight=False, soft_wrap=True)
        if decision:
            self.out.print(
                f"  a person must decide ({decision.get('kind') or 'decision'}), in the console: "
                f"{decision['decision_url']} — still waiting",
                markup=False,
                highlight=False,
                soft_wrap=True,
            )


def save_artifacts(task: dict[str, Any], directory: Path) -> list[tuple[str, Path | None, str]]:
    """Write the task's artefacts in `directory`: text as text, a JSON object as `.json`, a file as is.

    An artefact the task only describes (too large, withheld) is not written: the console serves it.
    Returns (artefact, written path or None, why not)."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[tuple[str, Path | None, str]] = []
    for artifact in task.get("artifacts") or []:
        name = str(artifact.get("name") or artifact.get("artifactId") or "artifact")
        described = ((artifact.get("metadata") or {}).get("choregos") or {}).get("content")
        if described:
            written.append((name, None, str(described)))
            continue
        for part in artifact.get("parts") or []:
            kind = part.get("kind")
            if kind == "file":
                file = part.get("file") or {}
                target = _inside(directory, str(file.get("name") or name))
                target.write_bytes(base64.b64decode(file.get("bytes") or ""))
            elif kind == "text":
                target = _inside(directory, name if Path(name).suffix else f"{name}.md")
                target.write_text(str(part.get("text") or ""), encoding="utf-8")
            elif kind == "data":
                target = _inside(directory, name if Path(name).suffix == ".json" else f"{name}.json")
                target.write_text(
                    json.dumps(part.get("data"), indent=2, ensure_ascii=False), encoding="utf-8"
                )
            else:
                continue
            written.append((name, target, ""))
    return written


def _inside(directory: Path, name: str) -> Path:
    """A file name of the door's, kept inside `directory` whatever it says."""
    base = Path(name).name
    if base in {"", ".", ".."}:
        base = "artifact"
    return directory / base


# ───────────────────────── commands ─────────────────────────

OrgOption = Annotated[str | None, typer.Option("--org", help="the organisation; the profile's otherwise")]
TokenOption = Annotated[
    str | None,
    typer.Option(
        "--token",
        envvar="CHOREGOS_A2A_TOKEN",
        show_envvar=True,
        help="an `a2a` token; the profile's token otherwise, which the door refuses when it is `*`",
    ),
]
JsonOption = Annotated[
    bool, typer.Option("--json", help="print the task as the door answers it, for a script")
]
OutOption = Annotated[
    Path | None, typer.Option("--out", help="write the artefacts of a completed task in this directory")
]


def _door(org: str | None, token: str | None) -> A2aDoor:
    profile = Profile.load()
    try:
        organisation = profile.organisation(org)
    except NoOrganisationError as exc:
        errors.print(f"[red]✗[/red] {exc}")
        raise typer.Exit(EXIT_REFUSED) from exc
    return A2aDoor(profile.api_url, organisation, token or profile.token)


def _run[T](door: A2aDoor, work: Callable[[A2aDoor], Coroutine[Any, Any, T]]) -> T:
    """Run one command against the door; a refusal is a red line on stderr and exit code 1."""

    async def main() -> T:
        async with door:
            return await work(door)

    try:
        return asyncio.run(main())
    except DoorError as refused:
        errors.print("[red]✗[/red] ", end="")
        errors.print(refused.explain(), markup=False, highlight=False, soft_wrap=True)
        raise typer.Exit(EXIT_REFUSED) from refused


def _print_json(value: Any) -> None:
    sys.stdout.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _summary(task: dict[str, Any], out: Console) -> None:
    choregos = (task.get("metadata") or {}).get("choregos") or {}
    key = f", {choregos['key']}" if choregos.get("key") else ""
    where = f", workflow state `{choregos['state']}`" if choregos.get("state") else ""
    out.print(f"task {task.get('id')} — {state_of(task)}{key}{where}", markup=False, highlight=False)
    if choregos.get("console_url"):
        out.print(f"  console: {choregos['console_url']}", markup=False, highlight=False, soft_wrap=True)
    decision = decision_of(task)
    if decision:
        out.print(
            f"  a person must decide, in the console: {decision['decision_url']}",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
    for artifact in task.get("artifacts") or []:
        out.print(f"  artefact: {artifact.get('name')}", markup=False, highlight=False)


def _finish(task: dict[str, Any], *, as_json: bool, out_dir: Path | None, out: Console) -> None:
    if out_dir is not None and state_of(task) == "completed":
        for name, path, why in save_artifacts(task, out_dir):
            line = f"  wrote {path}" if path else f"  {name} not written: {why}"
            out.print(line, markup=False, highlight=False, soft_wrap=True)
    if as_json:
        _print_json(task)
    else:
        _summary(task, out)


@a2a_app.command("skills")
def skills(org: OrgOption = None, token: TokenOption = None, as_json: JsonOption = False) -> None:
    """The workflows this token may start, and the fields each takes (`*` = required)."""
    found = skills_of(_run(_door(org, token), lambda door: door.card()))
    if as_json:
        _print_json(found)
        return
    if not found:
        console.print("no skill: no workflow of a project this token can read has opted in (`a2a:`)")
        return
    table = Table("skill", "description", "takes", "fields")
    for skill in found:
        table.add_row(
            str(skill.get("id")),
            str(skill.get("description") or ""),
            ", ".join(skill.get("inputModes") or []),
            describe_fields(skill.get("fields")),
        )
    console.print(table)


@a2a_app.command("send")
def send(  # noqa: PLR0913 - one option per part of the message, as the guide names them
    skill: Annotated[
        str, typer.Argument(help="the skill, `project/skill` as `choregos a2a skills` lists it")
    ],
    text: Annotated[
        list[str] | None,
        typer.Option("--text", help="the request in words, the work item's body (repeatable)"),
    ] = None,
    field: Annotated[
        list[str] | None,
        typer.Option("--field", help="`key=value` (repeatable), a value read as JSON when it is JSON"),
    ] = None,
    fields_json: Annotated[
        Path | None,
        typer.Option("--fields-json", help="a file holding the fields as one JSON object (`-` for stdin)"),
    ] = None,
    file: Annotated[
        list[str] | None,
        typer.Option("--file", help="`PATH` or `PATH;type=MEDIA/TYPE`, sent in base64 (repeatable)"),
    ] = None,
    message_id: Annotated[
        str | None,
        typer.Option(
            "--message-id",
            help="the idempotency key; by default a digest of the message, so the same command finds "
            "the same task",
        ),
    ] = None,
    title: Annotated[str | None, typer.Option("--title", help="the work item's title")] = None,
    wait: Annotated[
        bool,
        typer.Option("--wait", help="follow the task to its end: exit 0 completed, 3 failed, 4 canceled"),
    ] = False,
    out_dir: OutOption = None,
    org: OrgOption = None,
    token: TokenOption = None,
    as_json: JsonOption = False,
) -> None:
    """Start a workflow: a text, fields and files in one message. The message id is printed first."""
    try:
        fields = _read_fields_json(fields_json) if fields_json else {}
        fields.update(parse_fields(list(field or [])))
        files = [file_part(spec) for spec in file or []]
    except ValueError as exc:
        errors.print(f"[red]✗[/red] {exc}", highlight=False)
        raise typer.Exit(2) from exc
    if not (text or fields or files):
        errors.print("[red]✗[/red] nothing to send: give --text, --field, --fields-json or --file")
        raise typer.Exit(2)
    message = build_message(
        skill, texts=list(text or []), fields=fields, files=files, title=title, message_id=message_id
    )
    out = errors if as_json else console
    errors.print(f"message id: {message['messageId']}", markup=False, highlight=False)

    async def work(door: A2aDoor) -> dict[str, Any]:
        task = await door.call("message/send", {"message": message})
        if not wait or is_final(task):
            return task
        _summary(task, out)
        printer = Printer(out)
        printer.seen = printer.mark(task)
        return await follow(door, str(task["id"]), printer)

    try:
        task = _run(_door(org, token), work)
    except KeyboardInterrupt:
        errors.print("interrupted: `choregos a2a follow <task>` picks it up again")
        raise typer.Exit(EXIT_INTERRUPTED) from None
    _finish(task, as_json=as_json, out_dir=out_dir, out=out)
    if wait:
        raise typer.Exit(exit_code(task))


def _read_fields_json(path: Path) -> dict[str, Any]:
    source = sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")
    try:
        fields = json.loads(source)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--fields-json {path}: not JSON ({exc})") from exc
    if not isinstance(fields, dict):
        raise ValueError(f"--fields-json {path}: one JSON object is expected")
    return fields


@a2a_app.command("get")
def get(
    task: Annotated[str, typer.Argument(help="the task id `send` printed")],
    out_dir: OutOption = None,
    org: OrgOption = None,
    token: TokenOption = None,
    as_json: JsonOption = False,
) -> None:
    """Read a task once: its state, the decision it waits for, its artefacts."""
    found = _run(_door(org, token), lambda door: door.call("tasks/get", {"id": task}))
    _finish(found, as_json=as_json, out_dir=out_dir, out=errors if as_json else console)


@a2a_app.command("follow")
def follow_command(
    task: Annotated[str, typer.Argument(help="the task id `send` printed")],
    out_dir: OutOption = None,
    org: OrgOption = None,
    token: TokenOption = None,
    as_json: JsonOption = False,
) -> None:
    """Follow a task to its end, over the stream, opened again when the door cuts it (an hour at most).

    Exit 0 completed, 3 failed, 4 canceled. A decision is printed with its URL; the task waits for it."""
    out = errors if as_json else console
    try:
        found = _run(_door(org, token), lambda door: follow(door, task, Printer(out)))
    except KeyboardInterrupt:
        errors.print("interrupted")
        raise typer.Exit(EXIT_INTERRUPTED) from None
    _finish(found, as_json=as_json, out_dir=out_dir, out=out)
    raise typer.Exit(exit_code(found))


@a2a_app.command("cancel")
def cancel(
    task: Annotated[str, typer.Argument(help="the task id `send` printed")],
    org: OrgOption = None,
    token: TokenOption = None,
    as_json: JsonOption = False,
) -> None:
    """Stop a task: its work item is stopped, as from the console."""
    found = _run(_door(org, token), lambda door: door.call("tasks/cancel", {"id": task}))
    if as_json:
        _print_json(found)
        return
    console.print(f"[green]✓[/green] stop sent to task {task}", highlight=False)
    _summary(found, console)
