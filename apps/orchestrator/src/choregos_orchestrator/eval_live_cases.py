# SPDX-License-Identifier: Apache-2.0
"""An agent case played LIVE in the eval matrix (S57-11a, ADR 0122 §9): what its eval project holds, and
how its run is checked. Pure: the seeding and the judge of `activities/evals.py` write and read the rows.

A live cell plays an agent case on a deployment whose model is the real gateway and whose connectors are
fake scenes (`choregos_adapters.fakes.scenes`). Each case gets an eval project of its own, beside the
matrix's eval project and in its organisation (`case_project_slug`), because what the case decides is the
project's: its connectors, its policy, its agent.

- **Connectors.** One organisation connector per connector of the case, of the type its kind's scenes run
  (`scene_type`), its configuration `{fake_scene: <scene>}` and nothing else (no address: it reaches no
  network), available to the selected projects only and turned on for the case's eval project alone. The
  API serves it from the scene when the deployment serves eval fakes (`CHOREGOS_EVAL_FAKE_SCENES`); a
  connector of the same name that is not the matrix's, or that holds another scene, is never touched: the
  case is skipped and says why.
- **Web.** A case's `web:` (its search results and pages, #1378) is a fake web: one `web_search`
  organisation connector of type `fake` per case (`case_web`), its configuration `{fake_scene: web,
  fake_web: <the case's web>}`, available to the selected projects only and turned on for the case's eval
  project alone. Where the deployment serves eval fakes the API serves that project's `web_search` from
  the case's results and its `web_fetch` from the case's pages (their hosts resolve, no other does); the
  project's policy allows those hosts. The organisation's ceiling must open web search, as for any
  backend: the matrix does not open it.
- **Policy.** The matrix's eval project's policy, memory off, the case's `prompt_injection`, the hosts of its
  pages allowed when it has a `web:`; the tools door stays the cell's (`StagePlan.eval_cell.tools`). The
  case's `writes` is the project's: an operation that writes is `allowed` on the connector and tightened
  to `approval` in the case's project when the case says `writes: approval` (the default), the way any
  project tightens what its organisation allows.
- **Agent.** A catalogue agent (`netops`, `researcher`) is resolved as for any run: the organisation
  installs it. A case-local agent (`agent_spec`) is registered as a PROJECT agent of the case's eval
  project, one version per distinct spec, its connectors the case's (`case_agent_spec`).

**Governance parity** (the plan's §4.2 and §5.1): a live run counts only if what governs its calls agrees
on how many there were — its booked `tool` lines of the ledger (a provisional `in_flight` line is not a
call), its `choregos.tool.called` events, and the tool bridge's `tools.script.call` lines for the calls
that came from a script (the MCP door has no journal of its own: its calls are the events of origin
`model`). `parity` returns why they disagree; a run whose parity is broken is `invalid`: left out of the
cell's rates and of every comparison, and reported, never counted.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: The role an agent case's stage runs as: an agent's own step, no playbook of a software role.
LIVE_ROLE = "custom"
#: Who the matrix's rows say made them (`created_by`).
EVAL_CREATOR = "eval-matrix"
#: The project an agent case's decision paths name in the scripted matrix (`eval_doors.PROJECT`): a live
#: answer's are its eval project's, read under that name so that the case's facts hold for both.
SCRIPTED_PROJECT = "evals"
#: The limits and budget of a case-local agent's version: what one case may cost, never a model's call.
CASE_AGENT_LIMITS = {"max_turns": 40, "max_minutes": 20}
CASE_AGENT_BUDGET = {"run_usd": 3.0}
#: The ledger's provisional line (`choregos_core.tool_ledger.IN_FLIGHT`): a place under the cap, not a call.
IN_FLIGHT = "in_flight"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


@dataclass(frozen=True)
class CaseConnector:
    """A connector of a case, as its eval project declares it."""

    name: str
    kind: str
    type: str
    scene: str

    @property
    def config(self) -> dict[str, str]:
        from choregos_adapters.registry import FAKE_SCENE_FIELD

        return {FAKE_SCENE_FIELD: self.scene}


@dataclass(frozen=True)
class CaseWeb:
    """A case's fake web, as its eval project's organisation declares it (#1378)."""

    name: str
    web: str  # the case's `web:`, canonical JSON (a frozen value)

    kind = "web_search"
    type = "fake"

    @property
    def config(self) -> dict[str, Any]:
        from choregos_adapters.fakes.scenes import FAKE_SCENE_FIELD, WEB_FIELD, WEB_SCENE

        return {FAKE_SCENE_FIELD: WEB_SCENE, WEB_FIELD: json.loads(self.web)}

    @property
    def hosts(self) -> list[str]:
        from choregos_adapters.fakes.scenes import web_scene

        return web_scene(json.loads(self.web)).hosts


def case_web(case: Mapping[str, Any]) -> CaseWeb | None:
    """The case's fake web, `None` for a case without `web:`: a connector named `web-<case>` (or
    `web-<digest>` when that is not a name), so that two cases never share one."""
    web = case.get("web")
    if not web:
        return None
    name = f"web-{case['name']}"
    if not _SLUG.fullmatch(name):
        name = "web-" + hashlib.sha256(str(case["name"]).encode()).hexdigest()[:10]
    return CaseWeb(name, json.dumps(web, sort_keys=True, ensure_ascii=False))


def case_project_slug(eval_slug: str, case_name: str) -> str:
    """The case's eval project: `<eval>-<case>`, or `<eval>-<digest>` when that is not a slug."""
    slug = f"{eval_slug}-{case_name}"
    if _SLUG.fullmatch(slug):
        return slug
    digest = hashlib.sha256(slug.encode()).hexdigest()[:10]
    return f"{eval_slug[: 63 - len(digest) - 1]}-{digest}"


def live_refusal(case: Mapping[str, Any]) -> str:
    """Why a live cell cannot play the case, `""` when it can."""
    if not case.get("connectors") and not case.get("web"):
        return "the case names no connector and no web: a live cell has nothing to bind to a fake scene"
    return ""


def case_connectors(case: Mapping[str, Any]) -> list[CaseConnector]:
    """The case's connectors, each of the type its kind's scenes run; raises `AdapterError` for a kind or
    a scene the fakes do not have."""
    from choregos_adapters.fakes.scenes import scene, scene_type

    found: list[CaseConnector] = []
    for name, declared in sorted((case.get("connectors") or {}).items()):
        kind, named = str(declared["kind"]), str(declared.get("scene", "default"))
        scene(kind, named)  # an unknown kind or scene is refused here, before any row is written
        found.append(CaseConnector(str(name), kind, scene_type(kind), named))
    return found


def writes_need_approval(case: Mapping[str, Any]) -> bool:
    """`policy.writes`: `approval` (the default) or `allowed`."""
    return str((case.get("policy") or {}).get("writes", "approval")) != "allowed"


def case_policy(eval_policy: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    """The case's eval project's policy: the matrix's eval project's, memory off, the case's injection
    guard, no tools section (the cell's door is forced in the stage input)."""
    document: dict[str, Any] = json.loads(json.dumps(dict(eval_policy)))
    document["memory"] = {**(document.get("memory") or {}), "enabled": False}
    document.pop("tools", None)
    injection = (case.get("policy") or {}).get("prompt_injection")
    if injection is not None:
        document["sandbox"] = {**(document.get("sandbox") or {}), "prompt_injection": str(injection)}
    web = case_web(case)
    if web is not None:
        # `web_fetch` reads what the run's network allows: the hosts of the case's pages, and the tool is
        # offered (#1378).
        sandbox = dict(document.get("sandbox") or {})
        network = dict(sandbox.get("network") or {})
        network["allow_domains"] = sorted({*(network.get("allow_domains") or []), *web.hosts})
        sandbox["network"] = network
        document["sandbox"] = sandbox
        document["web_search"] = {**(document.get("web_search") or {}), "mode": "allow"}
    return document


def case_agent_spec(case: Mapping[str, Any], connectors: Iterable[CaseConnector]) -> dict[str, Any] | None:
    """The version a case-local agent (`agent_spec`) is registered with, `None` for a catalogue agent. It
    reads every operation of the case's connectors (the case's policy decides what a call does), clones
    nothing and keeps no memory."""
    own = case.get("agent_spec")
    if not own:
        return None
    return {
        "instructions": str(own.get("instructions", "")),
        "limits": dict(CASE_AGENT_LIMITS),
        "budget": dict(CASE_AGENT_BUDGET),
        "mcp_servers": [{"connector": c.name, "tools": ["*"]} for c in connectors],
        "memory": "none",
        "workspace": "none",
    }


def case_agent_slug(case: Mapping[str, Any]) -> str:
    """The agent a case's run names: its own's slug, else the catalogue agent's."""
    return str((case.get("agent_spec") or {}).get("slug") or case["agent"])


def spec_checksum(spec: Mapping[str, Any]) -> str:
    """The digest of a version's spec, canonical JSON: the same spec is the same version."""
    blob = json.dumps(dict(spec), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def live_answer(case: Mapping[str, Any], result: Mapping[str, Any] | None, project_slug: str) -> str:
    """What the run answered, for the case's judge: its output (`case.output`), else its summary. A
    decision path names the case's eval project (`/p/<slug>/actions/…`); it is read as the scripted
    matrix's (`/p/evals/actions/…`), the only difference between the two."""
    result = result or {}
    output = case.get("output")
    produced = (result.get("outputs") or {}).get(output) if output else None
    answer = produced if isinstance(produced, str) else str(result.get("summary") or "")
    return answer.replace(f"/p/{project_slug}/", f"/p/{SCRIPTED_PROJECT}/")


def parity(
    booked: Iterable[Mapping[str, Any]],
    called: Iterable[Mapping[str, Any]],
    relayed: Iterable[Mapping[str, Any]],
) -> str:
    """Why a run's governance records disagree on its tool calls, `""` when they agree.

    - `booked`: its `tool` lines of the ledger (`{provider, model, origin}`), `in_flight` ones left out;
    - `called`: its `choregos.tool.called` events' payloads (`{tool, origin, status_code, script_sha256}`);
    - `relayed`: its `tools.script.call` lines (`{tool, status_code, script_sha256}`).

    Every call booked has its event and every event its line, per tool and door. Every event of a call
    from a script has its bridge line (same tool, digest and outcome); a bridge line without an event is
    a call the API refused before booking it, so its outcome is 400 or more (a 404, the cap, a schema
    refusal, a timeout): a line that says the call was served (below 400) with no event is a call the
    platform did not record. A call the network policy refused has an event and no line (ADR 0070): it
    reads as a mismatch, the conservative way; the fake scenes reach no network.
    """
    lines = Counter(
        (str(line.get("model") or ""), str(line.get("origin") or "model"))
        for line in booked
        if line.get("provider") != IN_FLIGHT
    )
    called = list(called)
    events = Counter((str(e.get("tool") or ""), str(e.get("origin") or "model")) for e in called)
    for tool, origin in sorted(set(lines) | set(events)):
        if lines[(tool, origin)] != events[(tool, origin)]:
            return (
                f"governance parity broken: {lines[(tool, origin)]} ledger line(s) and "
                f"{events[(tool, origin)]} choregos.tool.called event(s) for {tool} from {origin}"
            )
    from_scripts = Counter(_relayed_key(e) for e in called if str(e.get("origin") or "model") == "script")
    bridge = Counter(_relayed_key(line) for line in relayed)
    for (tool, script, code), count in sorted(from_scripts.items(), key=str):
        if bridge[(tool, script, code)] < count:
            return (
                f"governance parity broken: {count} call(s) of {tool} from a script answered {code} have "
                f"{bridge[(tool, script, code)]} tools.script.call line(s)"
            )
    for (tool, script, code), count in sorted(bridge.items(), key=str):
        if count > from_scripts[(tool, script, code)] and code < 400:
            return (
                f"governance parity broken: the tool bridge relayed {tool} answered {code} "
                f"{count} time(s), the API recorded {from_scripts[(tool, script, code)]}"
            )
    return ""


def _relayed_key(entry: Mapping[str, Any]) -> tuple[str, str, int]:
    code = entry.get("status_code")
    return (
        str(entry.get("tool") or ""),
        str(entry.get("script_sha256") or ""),
        int(code) if isinstance(code, int) and not isinstance(code, bool) else 0,
    )


__all__ = [
    "CASE_AGENT_BUDGET",
    "CASE_AGENT_LIMITS",
    "EVAL_CREATOR",
    "LIVE_ROLE",
    "SCRIPTED_PROJECT",
    "CaseConnector",
    "CaseWeb",
    "case_agent_slug",
    "case_agent_spec",
    "case_connectors",
    "case_policy",
    "case_project_slug",
    "case_web",
    "live_answer",
    "live_refusal",
    "parity",
    "spec_checksum",
    "writes_need_approval",
]
