# SPDX-License-Identifier: Apache-2.0
"""What the agent reads of a call the tool server relays to the internal API (S57-04, ADR 0122 §8).

`POST /internal/runs/{id}/tools/{name}` answers `{status_code, result, remaining, suspicions?}`. The
model reads one text, so each part of that answer that changes what the result means is said in it,
in words, where the cut cannot reach it:

- a `202` whose result names an `action` did nothing yet: `pending_approval` is a proposal a person
  decides (ADR 0035), any other status a governed write that has not finished;
- `suspicions` are the injection guard's flags (`sandbox.prompt_injection: warn`): said first, before
  the result is read;
- `remaining` is what the run's tool-call cap leaves: said last.

The result is read as a script reads it through the tool bridge (#1374): compact JSON, its data
once — a text block that serializes the result's `structuredContent` (a connector's answer as the
broker wraps it, `web_search`) is left out, the structure says it; a text that says something else
(`web_fetch`'s page) stays — and as much of it as the platform lets through: the API bounds a result
with its data counted the same way and says the bound in its answer (`result_max_bytes`: an
operation's or a plugin tool's own cap, the platform's default for a page, #1383), and this JSON is
cut only past that many characters — `RESULT_MAX_BYTES` (`CHOREGOS_BROKER_RESULT_MAX_BYTES`' default)
when the answer names none —, the notices beside it and never cut. A provider's 4xx is an answer,
not an outage: it is flagged as an error, its body handed back. `structuredContent` is the API's
answer as it came, `status_code` included, plus the same `notices` for a client that hands the model
the structure rather than the text, when its JSON fits the same room: half an object is not the
object (the broker's rule), so a cut answer has none. The bound is the tool server's business: the
model is not told it.

A hosted session's call (#1349) is answered by `POST /internal/agent-sessions/{id}/tools/{name}` with
`{status_code, text, remaining, suspicions}`: the text is already what the session's model reads (the
courtier's text, S40-18c), so `render_session_answer` puts the same notices around it, with the same
error flag; the calls left are the session's, which the cap counts.

The notices, the cut and the error flag are composed by `choregos_core.tool_answer`, which a prompt
or realtime session's loop in the API renders through too (#1399): every door says the same thing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from choregos_core.mcp import data_once
from choregos_core.tool_answer import BOUND_KEY, MAX_NOTICES, MAX_TEXT, PENDING_APPROVAL, bound_of, render


def render_answer(answer: Mapping[str, Any]) -> dict[str, Any]:
    """The MCP `tools/call` result of a relayed call: its notices, its result as JSON, the calls left."""
    body = json.dumps(
        _read_once(answer.get("result")), ensure_ascii=False, separators=(",", ":"), default=str
    )
    return render(answer, body, "run")


def render_session_answer(answer: Mapping[str, Any]) -> dict[str, Any]:
    """The MCP `tools/call` result of a hosted session's call (#1349): the API's text, with the same
    notices and the same error flag as a run's call. An answer without `status_code` (an API before
    #1349) has no notice and is not an error: its text, as before."""
    return render(answer, str(answer.get("text") or ""), "session")


def _read_once(result: Any) -> Any:
    """The result as the model reads it: an MCP result with its data once (`data_once`), without a
    `content` left empty by it; any other JSON as it came."""
    if not isinstance(result, Mapping) or not isinstance(result.get("content"), list):
        return result
    whole = dict(result)
    once = data_once(whole)
    if once is whole or once["content"]:
        return once
    return {key: value for key, value in once.items() if key != "content"}


__all__ = [
    "BOUND_KEY",
    "MAX_NOTICES",
    "MAX_TEXT",
    "PENDING_APPROVAL",
    "bound_of",
    "render_answer",
    "render_session_answer",
]
