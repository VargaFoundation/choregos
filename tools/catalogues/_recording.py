"""Record and replay the HTTP exchanges a catalogue generator reads (ADR 0065).

A catalogue is generated from the official MCP registry, the GitHub API and tarballs of public
repositories. Reading them live on every run would make the committed file depend on the day
it is regenerated, and CI could not check it. So every exchange goes through one of two
transports:

- `RecordTransport` (`--record`, a maintainer with network) performs the request and writes the
  response to a JSON file under the recording directory;
- `ReplayTransport` (the default) reads only those files and **refuses the network**: a request
  with no recording raises `RecordingMissError` instead of going out.

A recording is keyed by the method, the URL and the request body, never by a header (the
`Authorization` header is neither stored nor part of the key), so the same request always finds
the same file. File names are `<readable slug>-<sha256 prefix>.json`, which keeps a directory
listing legible and a name collision impossible.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx

#: Response headers worth keeping. The rest (cookies, rate-limit counters, dates) would make a
#: re-recording differ for no reason.
KEPT_HEADERS = ("content-type",)


class RecordingMissError(RuntimeError):
    """A replayed run asked for an exchange nobody recorded: the generator needs `--record`."""


def exchange_key(method: str, url: str, body: bytes = b"") -> str:
    """The stable identity of a request: method, URL and body."""
    digest = hashlib.sha256()
    digest.update(method.upper().encode())
    digest.update(b"\0")
    digest.update(url.encode())
    digest.update(b"\0")
    digest.update(body)
    return digest.hexdigest()


def _slug(url: str) -> str:
    cleaned = re.sub(r"^https?://", "", url)
    return re.sub(r"[^A-Za-z0-9]+", "-", cleaned).strip("-").lower()[:60]


def recording_path(directory: Path, method: str, url: str, body: bytes = b"") -> Path:
    return directory / f"{_slug(url)}-{exchange_key(method, url, body)[:12]}.json"


def _encode_body(content: bytes) -> dict[str, str]:
    try:
        return {"body": content.decode("utf-8")}
    except UnicodeDecodeError:
        return {"body_b64": base64.b64encode(content).decode("ascii")}


def _decode_body(record: dict[str, Any]) -> bytes:
    if "body_b64" in record:
        return base64.b64decode(record["body_b64"])
    return str(record.get("body", "")).encode("utf-8")


class ReplayTransport(httpx.BaseTransport):
    """Answers from recordings only. A miss raises; nothing ever goes to the network."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = recording_path(self.directory, request.method, str(request.url), request.content)
        if not path.is_file():
            raise RecordingMissError(
                f"no recording for {request.method} {request.url} (expected {path.name}); "
                "run the generator with --record to capture it"
            )
        record = json.loads(path.read_text(encoding="utf-8"))
        return httpx.Response(
            record["status"],
            headers=record.get("headers", {}),
            content=_decode_body(record),
            request=request,
        )


class RecordTransport(httpx.BaseTransport):
    """Performs the request through `inner` and writes what came back."""

    def __init__(self, directory: Path, inner: httpx.BaseTransport | None = None) -> None:
        self.directory = directory
        self.inner = inner or httpx.HTTPTransport(retries=2)
        self.recorded: set[Path] = set()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.inner.handle_request(request)
        response.read()
        path = recording_path(self.directory, request.method, str(request.url), request.content)
        record: dict[str, Any] = {
            "method": request.method,
            "url": str(request.url),
            "status": response.status_code,
            "headers": {k: v for k, v in response.headers.items() if k.lower() in KEPT_HEADERS},
            **_encode_body(response.content),
        }
        if request.content:
            record["request_body"] = request.content.decode("utf-8", errors="replace")
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        self.recorded.add(path)
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


def make_client(
    directory: Path,
    *,
    record: bool,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> httpx.Client:
    """A client that records (network) or replays (default, no network)."""
    transport: httpx.BaseTransport = RecordTransport(directory) if record else ReplayTransport(directory)
    return httpx.Client(transport=transport, headers=headers or {}, timeout=timeout, follow_redirects=True)
