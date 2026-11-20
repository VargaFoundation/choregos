# SPDX-License-Identifier: Apache-2.0
"""A scriptable Vault, as an `httpx` transport (ADR 0005): the transit engine and the auth methods.

It really encrypts: each key version is an AES-256-GCM key, the ciphertext is `vault:v<N>:<b64>`, a
decryption under a tampered ciphertext or another `context` fails with Vault's 400, and a version below
`min_decryption_version` is refused. Tokens carry a lease on a clock the tests move. It is a simulation
written from Vault's documented API; no real Vault was exercised.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from typing import Any

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class FakeVault:
    def __init__(self, *, mount: str = "transit", derived: bool = False) -> None:
        self.mount = mount
        self.derived = derived
        self.now = 0.0
        self.keys: dict[str, list[bytes]] = {}
        self.min_decryption_version: dict[str, int] = {}
        self.tokens: dict[str, float] = {}  # token -> expiry on `self.now`
        self.static_tokens: set[str] = set()
        self.k8s_roles: dict[str, set[str]] = {}  # role -> accepted service-account JWTs
        self.lease_s = 3600.0
        self.calls: list[tuple[str, str]] = []  # (method, path) of the transit calls
        self.logins = 0
        self.fail_next: list[int] = []  # statuses to answer before behaving
        self.namespaces: list[str | None] = []
        self._counter = 0

    # ───────────────────────────── scripting ─────────────────────────────

    def create_key(self, name: str) -> None:
        self.keys[name] = [AESGCM.generate_key(256)]
        self.min_decryption_version[name] = 1

    def rotate(self, name: str) -> int:
        self.keys[name].append(AESGCM.generate_key(256))
        return len(self.keys[name])

    def allow_token(self, token: str) -> None:
        self.static_tokens.add(token)

    def revoke_all_tokens(self) -> None:
        self.tokens.clear()
        self.static_tokens.clear()

    def clock(self) -> float:
        return self.now

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # ───────────────────────────── the server ─────────────────────────────

    @staticmethod
    def _answer(status: int, body: dict[str, Any]) -> httpx.Response:
        return httpx.Response(status, json=body)

    def _error(self, status: int, message: str) -> httpx.Response:
        return self._answer(status, {"errors": [message]})

    def _authorised(self, request: httpx.Request) -> bool:
        token = request.headers.get("X-Vault-Token", "")
        return token in self.static_tokens or self.tokens.get(token, -1.0) > self.now

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.namespaces.append(request.headers.get("X-Vault-Namespace"))
        path = request.url.path
        if self.fail_next:
            return self._error(self.fail_next.pop(0), "injected failure")
        if path.startswith("/v1/auth/") and path.endswith("/login"):
            return self._login(request)
        prefix = f"/v1/{self.mount}/"
        if not path.startswith(prefix):
            return self._error(404, "no handler for route")
        self.calls.append((request.method, path[len(prefix) :]))
        if not self._authorised(request):
            return self._error(403, "permission denied")
        operation, _, name = path[len(prefix) :].partition("/")
        if name not in self.keys:
            return self._error(404, "encryption key not found")
        body: dict[str, Any] = json.loads(request.content) if request.content else {}
        if operation == "keys" and request.method == "GET":
            return self._answer(
                200, {"data": {"latest_version": len(self.keys[name]), "derived": self.derived}}
            )
        handler: dict[str, Callable[[str, dict[str, Any]], httpx.Response]] = {
            "encrypt": self._encrypt,
            "decrypt": self._decrypt,
            "rewrap": self._rewrap,
        }
        if operation not in handler:
            return self._error(404, "unsupported path")
        return handler[operation](name, body)

    def _login(self, request: httpx.Request) -> httpx.Response:
        self.logins += 1
        body = json.loads(request.content)
        if body.get("jwt") not in self.k8s_roles.get(body.get("role", ""), set()):
            return self._error(403, "permission denied")
        self._counter += 1
        token = f"hvs.fake-{self._counter}"
        self.tokens[token] = self.now + self.lease_s
        return self._answer(
            200, {"auth": {"client_token": token, "lease_duration": int(self.lease_s), "renewable": True}}
        )

    def _context(self, body: dict[str, Any]) -> bytes | None:
        raw = body.get("context")
        if self.derived and raw is None:
            raise ValueError("missing context for a derived key")
        return base64.b64decode(raw) if raw is not None else None

    def _seal(self, name: str, plaintext: bytes, context: bytes | None) -> str:
        version = len(self.keys[name])
        nonce = os.urandom(12)
        sealed = AESGCM(self.keys[name][version - 1]).encrypt(nonce, plaintext, context or b"")
        return f"vault:v{version}:" + base64.b64encode(nonce + sealed).decode()

    def _open(self, name: str, ciphertext: str, context: bytes | None) -> bytes | httpx.Response:
        parts = ciphertext.split(":")
        if len(parts) != 3 or parts[0] != "vault" or not parts[1].startswith("v"):
            return self._error(400, "invalid ciphertext: no prefix")
        version = int(parts[1][1:])
        if version < self.min_decryption_version[name] or version > len(self.keys[name]):
            return self._error(400, "ciphertext or signature version is disallowed by policy (too old)")
        raw = base64.b64decode(parts[2])
        try:
            return AESGCM(self.keys[name][version - 1]).decrypt(raw[:12], raw[12:], context or b"")
        except InvalidTag:
            return self._error(400, "cipher: message authentication failed")

    def _encrypt(self, name: str, body: dict[str, Any]) -> httpx.Response:
        try:
            context = self._context(body)
        except ValueError as error:
            return self._error(400, str(error))
        ciphertext = self._seal(name, base64.b64decode(body["plaintext"]), context)
        return self._answer(200, {"data": {"ciphertext": ciphertext}})

    def _decrypt(self, name: str, body: dict[str, Any]) -> httpx.Response:
        try:
            context = self._context(body)
        except ValueError as error:
            return self._error(400, str(error))
        opened = self._open(name, body["ciphertext"], context)
        if isinstance(opened, httpx.Response):
            return opened
        return self._answer(200, {"data": {"plaintext": base64.b64encode(opened).decode()}})

    def _rewrap(self, name: str, body: dict[str, Any]) -> httpx.Response:
        try:
            context = self._context(body)
        except ValueError as error:
            return self._error(400, str(error))
        opened = self._open(name, body["ciphertext"], context)
        if isinstance(opened, httpx.Response):
            return opened
        return self._answer(200, {"data": {"ciphertext": self._seal(name, opened, context)}})
