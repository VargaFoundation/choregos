"""Client ACP headless : lance l'agent, tient la session, arbitre les permissions."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .protocol import (
    ACP_VERSION,
    FS_READ_TEXT_FILE,
    FS_WRITE_TEXT_FILE,
    INITIALIZE,
    PERMISSION_DENIED,
    PROTOCOL_VERSION,
    SESSION_CANCEL,
    SESSION_NEW,
    SESSION_PROMPT,
    SESSION_REQUEST_PERMISSION,
    SESSION_UPDATE,
    Notification,
    Request,
    Response,
    decode,
    error_response,
)

PermissionHandler = Callable[[dict[str, Any]], Awaitable[tuple[bool, str]]]
UpdateHandler = Callable[[dict[str, Any]], Awaitable[None]]


class AgentUnreachableError(RuntimeError):
    """Le backend n'a pas répondu à `initialize` : le backend est cassé, pas l'agent."""


class AgentProtocolError(RuntimeError):
    """L'agent a violé le protocole ACP."""


@dataclass
class PromptOutcome:
    stop_reason: str = "end_turn"
    turns: int = 0
    #: Fragments de texte reçus de l'agent. Zéro message ET zéro outil = le modèle n'a pas
    #: répondu : ce n'est pas un agent distrait, c'est un accès qui ne marche pas.
    messages: int = 0
    tool_calls: int = 0
    permission_denials: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)


def _option_id(params: dict[str, Any], allowed: bool) -> str:
    """L'identifiant d'option à RENVOYER, choisi parmi ceux que l'agent propose.

    ACP laisse l'agent nommer ses options : il annonce `{kind, optionId, name}`, et le
    client répond avec un `optionId` DE CETTE LISTE. Nous renvoyions `allow_once`, un nom
    que claude-code-acp n'offre pas — il propose `allow` (de genre `allow_once`). Notre
    « oui » ne correspondait donc à rien, l'agent le lisait comme un refus, et il écrivait
    dans sa transcription que ses écritures étaient « refusées par l'utilisateur » alors
    que le journal de la plateforme, lui, disait « autorisé ». Deux versions de la même
    étape, et aucune ne mentait.
    """
    # L'ordre compte : `once` d'abord. Une décision de garde-fou porte sur CET appel ;
    # répondre « toujours » donnerait à l'agent un blanc-seing pour tous les suivants,
    # que personne n'a accordé.
    voulus = ("allow_once", "allow_always") if allowed else ("reject_once", "reject_always")
    options = [o for o in (params.get("options") or []) if isinstance(o, dict)]
    for genre in voulus:
        for option in options:
            if option.get("kind") == genre and option.get("optionId"):
                return str(option["optionId"])
    # Aucune option du genre attendu : on retombe sur le nom canonique du genre, qui est
    # aussi l'identifiant employé par les agents qui ne renomment rien.
    return voulus[0]


class AcpClient:
    """Client ACP sur stdio. Aucune dépendance au backend : c'est le contrat qui compte."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        on_update: UpdateHandler | None = None,
        on_permission: PermissionHandler | None = None,
        read_timeout: float = 120.0,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = {**os.environ, **(env or {})}
        self.on_update = on_update
        self.on_permission = on_permission
        self.read_timeout = read_timeout
        self.process: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None
        self.capabilities: dict[str, Any] = {}
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Response]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self.stderr_tail: list[str] = []
        self.outcome = PromptOutcome()

    # ───────────────────────── cycle de vie ─────────────────────────

    async def start(self) -> None:
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=self.cwd,
                env=self.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise AgentUnreachableError(f"impossible de lancer {self.command[0]} : {exc}") from exc
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def stop(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10)
            except TimeoutError:
                self.process.kill()

    async def __aenter__(self) -> AcpClient:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # ───────────────────────── protocole ─────────────────────────

    async def initialize(self, client_capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.request(
            INITIALIZE,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "acpVersion": ACP_VERSION,
                # On annonce ce qu'on SERT, et on sert les deux méthodes fichier (plus bas).
                # Les deux mensonges possibles coûtent cher, et on les a payés tous les deux :
                # annoncer `fs` puis refuser l'appel envoie l'agent tout refaire en `bash`
                # (« le système de fichiers n'est pas exposé », dit sa transcription) ;
                # annoncer `false` fait désactiver ses outils d'édition, et il ne peut plus
                # rien écrire du tout.
                "clientCapabilities": client_capabilities
                or {"fs": {"readTextFile": True, "writeTextFile": True}, "terminal": False},
                "clientInfo": {"name": "choregos-runner", "version": "1.0.0"},
            },
            timeout=60.0,
        )
        if response.failed:
            raise AgentUnreachableError(f"initialize refusé : {response.error}")
        self.capabilities = (response.result or {}).get("agentCapabilities", {})
        return dict(response.result or {})

    async def new_session(self, cwd: str, mcp_servers: list[dict[str, Any]] | None = None) -> str:
        response = await self.request(
            SESSION_NEW, {"cwd": cwd, "mcpServers": mcp_servers or []}, timeout=120.0
        )
        if response.failed:
            raise AgentProtocolError(f"session/new refusé : {response.error}")
        session_id = (response.result or {}).get("sessionId")
        if not session_id:
            raise AgentProtocolError("session/new n'a pas rendu de sessionId")
        self.session_id = str(session_id)
        return self.session_id

    async def prompt(self, text: str, *, timeout: float) -> PromptOutcome:
        """Envoie un tour de prompt et laisse l'agent travailler jusqu'à `stopReason`."""
        if self.session_id is None:
            raise AgentProtocolError("aucune session ouverte")
        self.outcome.turns += 1
        try:
            response = await self.request(
                SESSION_PROMPT,
                {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]},
                timeout=timeout,
            )
        except TimeoutError:
            await self.cancel()
            self.outcome.stop_reason = "timeout"
            self.outcome.cancelled = True
            return self.outcome
        if response.failed:
            self.outcome.errors.append(str(response.error))
            self.outcome.stop_reason = "error"
            return self.outcome
        self.outcome.stop_reason = str((response.result or {}).get("stopReason", "end_turn"))
        return self.outcome

    async def cancel(self) -> None:
        if self.session_id is None or self.process is None:
            return
        with contextlib.suppress(Exception):
            await self.notify(SESSION_CANCEL, {"sessionId": self.session_id})
        self.outcome.cancelled = True

    async def request(self, method: str, params: dict[str, Any], *, timeout: float = 60.0) -> Response:
        if self.process is None or self.process.stdin is None:
            raise AgentUnreachableError("processus agent absent")
        self._next_id += 1
        request = Request(id=self._next_id, method=method, params=params)
        future: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
        self._pending[request.id] = future
        self.process.stdin.write(request.encode())
        await self.process.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request.id, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            return
        self.process.stdin.write(Notification(method=method, params=params).encode())
        await self.process.stdin.drain()

    async def respond(self, response: Response) -> None:
        if self.process is None or self.process.stdin is None:
            return
        self.process.stdin.write(response.encode())
        await self.process.stdin.drain()

    # ───────────────────────── boucle de lecture ─────────────────────────

    async def _read_loop(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            message = decode(line)
            if message is None:
                continue
            if isinstance(message, Response):
                future = self._pending.get(message.id)
                if future is not None and not future.done():
                    future.set_result(message)
                continue
            if isinstance(message, Notification):
                await self._handle_notification(message)
                continue
            await self._handle_request(message)

    async def _handle_notification(self, notification: Notification) -> None:
        if notification.method == SESSION_UPDATE:
            await self._on_update(notification.params)

    async def _handle_request(self, request: Request) -> None:
        if request.method == SESSION_REQUEST_PERMISSION:
            allowed, reason = await self._decide(request.params)
            choix = _option_id(request.params, allowed)
            if not allowed:
                self.outcome.permission_denials += 1
                await self.respond(
                    Response(
                        id=request.id,
                        result={"outcome": {"outcome": "selected", "optionId": choix}, "reason": reason},
                    )
                )
                return
            await self.respond(
                Response(id=request.id, result={"outcome": {"outcome": "selected", "optionId": choix}})
            )
            return
        if request.method == SESSION_UPDATE:
            await self._on_update(request.params)
            await self.respond(Response(id=request.id, result={}))
            return
        if request.method == FS_READ_TEXT_FILE:
            await self._read_text_file(request)
            return
        if request.method == FS_WRITE_TEXT_FILE:
            await self._write_text_file(request)
            return
        await self.respond(
            error_response(request.id, PERMISSION_DENIED, f"méthode non gérée : {request.method}")
        )

    def _resolve(self, raw: Any) -> Path | None:
        """Un chemin du workspace, ou rien.

        Le workspace est la frontière : un chemin relatif s'y rattache, un chemin absolu
        doit y tomber. Ce qui sort — `../../etc/passwd`, un lien qui remonte — n'est pas
        une erreur de l'agent à corriger, c'est une demande à refuser.
        """
        if not isinstance(raw, str) or not raw:
            return None
        racine = Path(self.cwd).resolve()
        cible = (racine / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        return cible if cible == racine or racine in cible.parents else None

    async def _read_text_file(self, request: Request) -> None:
        params = request.params or {}
        cible = self._resolve(params.get("path"))
        if cible is None:
            await self.respond(error_response(request.id, PERMISSION_DENIED, "chemin hors du workspace"))
            return
        try:
            lignes = cible.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError as exc:
            await self.respond(error_response(request.id, PERMISSION_DENIED, f"lecture impossible : {exc}"))
            return
        # `line` est 1-indexé dans ACP ; `limit` borne le nombre de lignes rendues.
        depart = max(int(params.get("line") or 1) - 1, 0)
        limite = params.get("limit")
        fin = depart + int(limite) if limite else len(lignes)
        await self.respond(Response(id=request.id, result={"content": "".join(lignes[depart:fin])}))

    async def _write_text_file(self, request: Request) -> None:
        params = request.params or {}
        cible = self._resolve(params.get("path"))
        if cible is None:
            await self.respond(error_response(request.id, PERMISSION_DENIED, "chemin hors du workspace"))
            return
        try:
            cible.parent.mkdir(parents=True, exist_ok=True)
            cible.write_text(str(params.get("content", "")), encoding="utf-8")
        except OSError as exc:
            await self.respond(error_response(request.id, PERMISSION_DENIED, f"écriture impossible : {exc}"))
            return
        await self.respond(Response(id=request.id, result={}))

    async def _on_update(self, params: dict[str, Any]) -> None:
        update = params.get("update", params)
        kind = str(update.get("sessionUpdate", update.get("type", "")))
        if kind in {"tool_call", "tool_call_update"} and update.get("status") != "in_progress":
            self.outcome.tool_calls += 1
        if kind in {"agent_message_chunk", "agent_thought_chunk"}:
            self.outcome.messages += 1
        if self.on_update is not None:
            await self.on_update(params)

    async def _decide(self, params: dict[str, Any]) -> tuple[bool, str]:
        if self.on_permission is None:
            return True, "aucune politique de permission : autorisé"
        return await self.on_permission(params)

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            self.stderr_tail.append(text)
            if len(self.stderr_tail) > 200:
                self.stderr_tail.pop(0)
