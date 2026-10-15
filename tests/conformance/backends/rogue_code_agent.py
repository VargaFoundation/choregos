"""An ACP agent that ignores the guard: it writes its deliverable without asking.

Not a test: `test_code_agents.py` starts it to prove the conformance suite can fail (this is what
an agent that skips the SDK's permission path looks like).
"""

import json
import sys
from pathlib import Path


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


INFO = {"protocolVersion": 1, "agentCapabilities": {}, "agentInfo": {"name": "rogue", "version": "0"}}
RESULT = {"schema": "choregos/StageResult/v1", "status": "done", "summary": "ok"}

for line in sys.stdin:
    message = json.loads(line)
    method, message_id = message.get("method"), message.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message_id, "result": INFO})
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": message_id, "result": {"sessionId": "s1"}})
    elif method == "session/prompt":
        outputs = Path(".choregos") / "outputs"
        outputs.mkdir(parents=True, exist_ok=True)
        (outputs / "x.md").write_text("written without asking")
        (Path(".choregos") / "result.json").write_text(json.dumps(RESULT))
        send({"jsonrpc": "2.0", "id": message_id, "result": {"stopReason": "end_turn"}})
