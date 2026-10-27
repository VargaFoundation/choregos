# SPDX-License-Identifier: Apache-2.0
"""Runs SkillSpector on a skill without the network (ADR 0095).

Started by the API with SkillSpector's own interpreter, never imported by the platform:
`<skillspector python> -I skillspector_offline.py <skill dir> <report.json> [<osv mirror>]`.

Every HTTP client SkillSpector builds gets a transport that answers OSV's two calls from the local
mirror (`osv_mirror`) and refuses everything else, so nothing leaves the pod: not `api.osv.dev`,
not a provider (the scan is `--no-llm`), not a URL a skill names. Without a mirror, OSV's calls
are refused too: SkillSpector then marks its supply-chain check incomplete, which the gate reads.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import osv_mirror  # type: ignore[import-not-found]  # beside this file, on the path set above

#: SkillSpector's OSV calls, as it builds them (`skillspector/nodes/analyzers/osv_client.py`).
OSV_BATCH = "/v1/querybatch"
OSV_VULNS = "/v1/vulns/"
OSV_HOST = "api.osv.dev"


def _transport(mirror: osv_mirror.Mirror | None) -> object:
    import httpx

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host != OSV_HOST or mirror is None:
            return httpx.Response(403, json={"error": "no network for the skill scanner (ADR 0095)"})
        if request.method == "POST" and request.url.path == OSV_BATCH:
            body = json.loads(request.content or b"{}")
            return httpx.Response(200, json=mirror.query_batch(body.get("queries") or []))
        if request.method == "GET" and request.url.path.startswith(OSV_VULNS):
            record = mirror.vuln(request.url.path[len(OSV_VULNS) :])
            return (
                httpx.Response(200, json=record)
                if record
                else httpx.Response(404, json={"message": "not found"})
            )
        return httpx.Response(404, json={"message": "not found"})

    return httpx.MockTransport(handle)


def _offline(mirror: osv_mirror.Mirror | None) -> None:
    """Every `httpx.Client` and `AsyncClient` built from now on answers from the mirror only."""
    import httpx

    transport = _transport(mirror)
    sync_init = httpx.Client.__init__

    def client_init(self: httpx.Client, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = transport
        kwargs.pop("proxy", None)
        sync_init(self, *args, **kwargs)  # type: ignore[arg-type]

    httpx.Client.__init__ = client_init  # type: ignore[method-assign]

    async_init = httpx.AsyncClient.__init__

    def async_client_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(403))
        kwargs.pop("proxy", None)
        async_init(self, *args, **kwargs)  # type: ignore[arg-type]

    httpx.AsyncClient.__init__ = async_client_init  # type: ignore[method-assign]


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("usage: skillspector_offline.py SKILL_DIR REPORT_JSON [OSV_MIRROR]", file=sys.stderr)
        return 2
    skill, report = argv[0], argv[1]
    mirror_path = Path(argv[2]) if len(argv) == 3 and argv[2] else None
    mirror = osv_mirror.Mirror(mirror_path) if mirror_path and mirror_path.exists() else None
    # Before SkillSpector is imported: no tracing, no provider, whatever the environment says.
    for name in [
        n for n in os.environ if n.startswith(("LANGCHAIN_", "LANGSMITH_", "SKILLSPECTOR_PROVIDER"))
    ]:
        del os.environ[name]
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    _offline(mirror)
    from skillspector.cli import app  # type: ignore[import-not-found]

    try:
        app(["scan", skill, "--no-llm", "--format", "json", "--output", report], standalone_mode=False)
    except SystemExit as exc:  # the verdict is in the exit code; the report is what we read
        return int(exc.code or 0) if isinstance(exc.code, int) else 1
    finally:
        if mirror is not None:
            mirror.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
