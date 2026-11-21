# SPDX-License-Identifier: Apache-2.0
"""The relay: the URL, the headers and the tool name a job step sends, and what a failure is."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from tranibor_procurement.relay import HttpRelay, RelayError, collect, parse_response, tool_name


def _serve(answer: bytes, code: int = 200) -> tuple[HTTPServer, dict[str, Any]]:
    seen: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            seen["path"] = self.path
            seen["auth"] = self.headers["Authorization"]
            seen["type"] = self.headers["Content-Type"]
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(code)
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def log_message(self, *a: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def _env(server: HTTPServer) -> dict[str, str]:
    return {
        "TRANIBOR_API_URL": f"http://127.0.0.1:{server.server_port}/",
        "TRANIBOR_RUN_ID": "r1",
        "TRANIBOR_RUN_TOKEN": "tok",
    }


def test_the_tool_is_named_connector_then_operation() -> None:
    assert tool_name("list_contracts") == "purchasing__list_contracts"


def test_the_relay_pins_the_url_the_headers_and_the_name() -> None:
    answer = json.dumps({"status_code": 200, "result": {"structuredContent": {"suppliers": []}}}).encode()
    server, seen = _serve(answer)
    try:
        got = HttpRelay(_env(server))("search_suppliers", {"name": "Alderbrook"})
    finally:
        server.shutdown()
    assert got == {"suppliers": []}
    assert seen["path"] == "/runs/r1/tools/purchasing__search_suppliers"
    assert seen["auth"] == "Bearer tok"
    assert seen["type"] == "application/json"
    assert seen["body"] == {"name": "Alderbrook"}


def test_a_refusal_of_the_api_is_an_error() -> None:
    server, _ = _serve(b'{"title": "Not found"}', code=404)
    try:
        with pytest.raises(RelayError, match="purchasing__list_contracts"):
            HttpRelay(_env(server))("list_contracts", {})
    finally:
        server.shutdown()


def test_the_environment_is_needed() -> None:
    with pytest.raises(RelayError, match="TRANIBOR_API_URL"):
        HttpRelay({})


@pytest.mark.parametrize("payload", [[], {"status_code": 403, "result": {}}, {"status_code": 200}])
def test_a_response_that_is_not_a_result_is_an_error(payload: object) -> None:
    with pytest.raises(RelayError):
        parse_response(payload, "t")


def test_pages_are_followed_and_an_answer_with_no_list_is_an_error() -> None:
    pages = {None: {"contracts": [{"id": "a"}], "next_cursor": "c2"}, "c2": {"contracts": [{"id": "b"}]}}
    calls: list[dict[str, Any]] = []

    def relay(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(arguments)
        return pages[arguments.get("cursor")]

    assert [r["id"] for r in collect(relay, "list_contracts", {}, "contracts")] == ["a", "b"]
    assert calls[1]["cursor"] == "c2"
    with pytest.raises(RelayError, match="no list"):
        collect(lambda o, a: {}, "list_contracts", {}, "contracts")
