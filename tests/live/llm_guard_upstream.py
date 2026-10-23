"""A fake OpenAI provider for `test_llm_guard_live.py`, run in a container next to LiteLLM.

Stdlib only. It records every request body (`GET /_seen`, `DELETE /_seen` clears) and answers with
secrets: in the text, in the model's reasoning, and in a tool call's arguments, on
`/chat/completions` and `/responses` (what LiteLLM calls for `/v1/messages` and `/v1/responses`),
streamed or not. Streams are cut every few characters, so that every secret straddles chunks.
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# Assembled at run time: gitleaks reads every branch.
SECRET = "AKIA" + "Q" * 16
PASSWORD = "hunter2" * 2
ANSWER = f"Here is the key {SECRET}\nand a clone https://bot:{PASSWORD}@git.example/a.git\nbye"
ARGUMENTS = json.dumps({"path": "k.txt", "content": SECRET})
REASONING = f"The file holds {SECRET}\nso I clone https://bot:{PASSWORD}@git.example/a.git\n"

SEEN: list[dict[str, Any]] = []


def _pieces(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _json(self, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _events(self, events: list[str]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in events:
            self.wfile.write(event.encode())
            self.wfile.flush()

    def do_GET(self) -> None:
        if self.path == "/_seen":
            self._json(SEEN)
            return
        if self.path.endswith("/models"):
            self._json({"data": [{"id": "gpt-5.5"}]})
            return
        self.send_error(404)

    def do_DELETE(self) -> None:
        SEEN.clear()
        self._json({})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        SEEN.append({"path": self.path, "body": body})
        model = body.get("model", "gpt-5.5")
        if self.path.endswith("/responses"):
            self._responses(model, bool(body.get("stream")))
            return
        if body.get("stream"):
            self._chat_stream(model)
            return
        message = {
            "role": "assistant",
            "content": ANSWER,
            "reasoning_content": REASONING,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": ARGUMENTS},
                }
            ],
        }
        self._json(
            {
                "id": "c1",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            }
        )

    def _chat_stream(self, model: str) -> None:
        def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
            choice = {"index": 0, "delta": delta, "finish_reason": finish}
            payload = {
                "id": "c1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": model,
                "choices": [choice],
            }
            return f"data: {json.dumps(payload)}\n\n"

        def call(function: dict[str, str], **head: str) -> dict[str, Any]:
            return {"tool_calls": [{"index": 0, **head, "function": function}]}

        events = [chunk({"role": "assistant", "content": ""})]
        events += [chunk({"reasoning_content": piece}) for piece in _pieces(REASONING, 6)]
        events += [chunk({"content": piece}) for piece in _pieces(ANSWER, 7)]
        events.append(chunk(call({"name": "write_file", "arguments": ""}, id="call_1", type="function")))
        events += [chunk(call({"arguments": piece})) for piece in _pieces(ARGUMENTS, 9)]
        events += [chunk({}, "tool_calls"), "data: [DONE]\n\n"]
        self._events(events)

    def _responses(self, model: str, stream: bool) -> None:
        summary = {"type": "summary_text", "text": REASONING}
        reasoning = {"type": "reasoning", "id": "rs_1", "summary": [summary], "encrypted_content": "opaque"}
        text = {"type": "output_text", "text": ANSWER, "annotations": []}
        message = {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "status": "completed",
            "content": [text],
        }
        call = {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "write_file",
            "arguments": ARGUMENTS,
            "status": "completed",
        }
        out = {
            "id": "resp_1",
            "object": "response",
            "created_at": int(time.time()),
            "model": model,
            "status": "completed",
            "output": [reasoning, message, call],
            "usage": {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10},
        }
        if not stream:
            self._json(out)
            return
        sequence = iter(range(1, 1000))

        def event(kind: str, **data: Any) -> str:
            body = {"type": kind, "sequence_number": next(sequence), **data}
            return f"event: {kind}\ndata: {json.dumps(body)}\n\n"

        events = [event("response.created", response={**out, "status": "in_progress", "output": []})]
        events.append(event("response.output_item.added", output_index=0, item={**reasoning, "summary": []}))
        rs: dict[str, Any] = {"item_id": "rs_1", "output_index": 0, "summary_index": 0}
        events.append(event("response.reasoning_summary_part.added", **rs, part={**summary, "text": ""}))
        events += [
            event("response.reasoning_summary_text.delta", **rs, delta=piece)
            for piece in _pieces(REASONING, 6)
        ]
        events.append(event("response.reasoning_summary_text.done", **rs, text=REASONING))
        events.append(event("response.reasoning_summary_part.done", **rs, part=summary))
        events.append(event("response.output_item.done", output_index=0, item=reasoning))
        events.append(event("response.output_item.added", output_index=1, item={**message, "content": []}))
        events += [
            event("response.output_text.delta", item_id="msg_1", output_index=1, content_index=0, delta=piece)
            for piece in _pieces(ANSWER, 7)
        ]
        events.append(
            event("response.output_text.done", item_id="msg_1", output_index=1, content_index=0, text=ANSWER)
        )
        events.append(event("response.output_item.done", output_index=1, item=message))
        events.append(event("response.output_item.added", output_index=2, item={**call, "arguments": ""}))
        events += [
            event("response.function_call_arguments.delta", item_id="fc_1", output_index=2, delta=piece)
            for piece in _pieces(ARGUMENTS, 9)
        ]
        events.append(
            event(
                "response.function_call_arguments.done", item_id="fc_1", output_index=2, arguments=ARGUMENTS
            )
        )
        events.append(event("response.output_item.done", output_index=2, item=call))
        events.append(event("response.completed", response=out))
        self._events(events)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 8080), Handler).serve_forever()
