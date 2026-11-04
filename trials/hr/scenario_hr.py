# SPDX-License-Identifier: Apache-2.0
"""The HR scenario on the dev tenant (S20-07): the `joiners-leavers` template, the fakes served by a
single pod (`demoFakes.enabled`), a real agent, real human decisions.

The dev tenant does not skip time: the default arrival date is YESTERDAY - D-10 and D-7 are past, D+1
is today -, and `arrival --in 30` shows the wait, which `move` undoes by bringing the date forward
(the timer re-arms). This script prepares, files and follows; it DECIDES nothing: a decision is
taken in the console, by a re-authenticated person - an API token proposes, it does not decide
(ADR 0030). The "badge" task too: a script does not attest to a hand-over in person.

    uv run python trials/hr/scenario_hr.py prepare        # connectors, policies, project
    uv run python trials/hr/scenario_hr.py arrival        # the request, through the MCP door
    uv run python trials/hr/scenario_hr.py follow RH-1    # what the work item waits for, and where to decide
    uv run python trials/hr/scenario_hr.py move RH-2 2026-10-04   # a date brought forward re-arms
    uv run python trials/hr/scenario_hr.py departure RH-1 # the same person's departure, on D0
    uv run python trials/hr/scenario_hr.py verify RH-1    # each action, its journal, the proof

Variables:
    CHOREGOS_DEV_URL     the address of the tenant's console (e.g. `https://choregos.example`)
    CHOREGOS_DEV_TOKEN   an administrator's API token (`chg_...`), created in the console; never written here
    CHOREGOS_DEV_ORG     the tenant's organisation
    TRIAL_FAKES_URL      (default http://choregos-demo-fakes:8090) the address of the fakes, seen from the cluster
    TRIAL_TOKEN_REF      (optional) the reference of the fakes' token, e.g. `env:CONNECTOR_DEMO_TOKEN`
    TRIAL_BACKEND        (default opencode) the backend of the project's agents, the one the dev tenant runs
    TRIAL_MODEL          (default platform/standard) the gateway model behind the `standard` profile
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import time
from typing import Any

import httpx

#: Required by `client()`: no tenant is assumed - Choregos is an open project.
URL = os.environ.get("CHOREGOS_DEV_URL", "").rstrip("/")
FAKES = os.environ.get("TRIAL_FAKES_URL", "http://choregos-demo-fakes:8090").rstrip("/")
#: The slug of the HR project; an English demo next to a `rh` born in French takes another name.
PROJECT = os.environ.get("TRIAL_HR_PROJECT", "rh")  # lang: fr-ok
TERMINAL = {"ready", "closed", "pret", "clos"}  # lang: fr-ok (the last two: items born from the 1.x template)
#: What the template's agents ask for (`model: profile:standard`), and the backend that runs them:
#: a new project gets `claude-code` and no profile, which the dev tenant does not serve - the first
#: run, on 06/10, saw the agent's plan fail twice without a single call to the model.
BACKEND = os.environ.get("TRIAL_BACKEND", "opencode")
MODEL = os.environ.get("TRIAL_MODEL", "platform/standard")


def require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"missing variable: {name} (see the header of this script)")
    return value


def client() -> httpx.Client:
    return httpx.Client(
        base_url=f"{require('CHOREGOS_DEV_URL').rstrip('/')}/api/v1",
        headers={"Authorization": f"Bearer {require('CHOREGOS_DEV_TOKEN')}"},
        timeout=60,
    )


def expect(response: httpx.Response, *expected: int) -> Any:
    if response.status_code not in expected:
        sys.exit(
            f"{response.request.method} {response.request.url.path} -> {response.status_code}: "
            f"{response.text[:400]}"
        )
    return response.json() if response.content else None


def _references() -> dict[str, str]:
    reference = os.environ.get("TRIAL_TOKEN_REF", "")
    return {"api_key": reference} if reference else {}


def prepare() -> None:
    """The administrator's gesture: five connectors, what the organisation allows, the project."""
    org = require("CHOREGOS_DEV_ORG")
    reference = os.environ.get("TRIAL_TOKEN_REF", "")
    connectors = [
        {"name": "directory", "kind": "identity", "type": "entra",
         "config": {"tenant_id": "demo", "client_id": "choregos",
                    "graph_url": f"{FAKES}/graph/v1.0", "login_url": f"{FAKES}/login"},
         **({"secret_refs": {"client_secret": reference}} if reference else {})},
        {"name": "devices", "kind": "mdm", "type": "demo", "config": {"url": f"{FAKES}/mdm/mcp"},
         **({"secret_refs": _references()} if reference else {})},
        {"name": "carrier", "kind": "shipping", "type": "demo",
         "config": {"url": f"{FAKES}/shipping/mcp"},
         **({"secret_refs": _references()} if reference else {})},
        {"name": "badge-readers", "kind": "access_control", "type": "demo",
         "config": {"url": f"{FAKES}/access_control/mcp"},
         **({"secret_refs": _references()} if reference else {})},
        {"name": "supplier", "kind": "mcp", "type": "mcp", "config": {"url": f"{FAKES}/supplier/mcp"},
         **({"secret_refs": {"token": reference}} if reference else {})},
    ]  # fmt: skip
    with client() as http:
        existing = {c["name"] for c in expect(http.get(f"/orgs/{org}/connectors"), 200)}
        for body in connectors:
            if body["name"] in existing:
                print(f"connector {body['name']}: already there")
                continue
            expect(http.post(f"/orgs/{org}/connectors", json=body), 201)
            print(f"connector {body['name']}: declared")
        expect(http.post(f"/orgs/{org}/connectors/supplier/discover"), 200)
        allowed = {
            "directory": [
                "create_user",
                "add_to_group",
                "remove_from_group",
                "disable_user",
                "revoke_sessions",
            ],
            "devices": ["enroll_device", "wipe_device"],
            "carrier": ["create_shipment", "create_return"],
            "badge-readers": ["activate_badge", "deactivate_badge"],
            "supplier": ["order_status"],
        }
        for connector, names in allowed.items():
            for name in names:
                expect(
                    http.patch(
                        f"/orgs/{org}/connectors/{connector}/operations/{name}", json={"policy": "allowed"}
                    ),
                    200,
                )
        expect(
            http.patch(
                f"/orgs/{org}/connectors/supplier/operations/order_laptop", json={"policy": "approval"}
            ),
            200,
        )
        print("policies: writes allowed, the laptop order under approval")
        projects = {p["slug"] for p in expect(http.get(f"/orgs/{org}/projects"), 200)["items"]}
        if PROJECT not in projects:
            body = {
                "slug": PROJECT,
                "name": "HR",
                "template_ref": "joiners-leavers",
                "config": {"slug": PROJECT, "org": org},
            }
            project = expect(http.post(f"/orgs/{org}/projects", json=body), 201)
            expect(
                http.put(
                    f"/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
                ),
                200,
            )
        # The agents' backend and model, set on every run: a project born before them keeps them.
        project = dict(expect(http.get(f"/projects/{org}:{PROJECT}"), 200))
        config = {
            **(project.get("config") or {}),
            "agent": {"default_backend": BACKEND, "allowed_backends": [BACKEND]},
            "models": {"profiles": {"standard": MODEL}},
        }
        expect(http.patch(f"/projects/{project['id']}", json={"config": config}), 200)
        print(f"project {PROJECT}: ready - agents on {BACKEND}, standard profile -> {MODEL} - {URL}/p/{PROJECT}")


def _project(http: httpx.Client) -> dict[str, Any]:
    org = require("CHOREGOS_DEV_ORG")
    return dict(expect(http.get(f"/projects/{org}:{PROJECT}"), 200))


def arrival(in_days: int = -1) -> None:
    """The request, filed as an HR person's Claude would: an `mcp:write` token for the duration of
    the call, revoked afterwards."""
    org = require("CHOREGOS_DEV_ORG")
    date = (dt.date.today() + dt.timedelta(days=in_days)).isoformat()
    fields = {"full_name": "Lea Martin", "upn": f"lea.{int(time.time())}@demo.test", "start_date": date,
              "job": "Developer", "group": "devs", "sensitive_group": "prod-read",
              "address": "12 Peace Street, Paris"}  # fmt: skip
    with client() as http:
        token = expect(http.post("/me/tokens", json={"name": "trial-hr", "scopes": ["mcp:write"]}), 201)
        try:
            call = httpx.post(
                f"{URL}/mcp/projects/{org}:{PROJECT}",
                headers={"Authorization": f"Bearer {token['token']}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "create_work_item",
                                 "arguments": {"title": f"Joiner: {fields['full_name']}",
                                               "workflow": "onboarding",
                                               "fields": fields}}},
                timeout=60,
            )  # fmt: skip
        finally:
            expect(http.delete(f"/me/tokens/{token['id']}"), 204, 200)
    result = expect(call, 200)["result"]
    if result.get("isError"):
        sys.exit(f"the MCP door refuses: {result}")
    print(
        f"work item {result['structuredContent']['key']} - arrival on {date} - "
        f"{result['structuredContent']['console_url']}"
    )


def _item(http: httpx.Client, key: str) -> dict[str, Any]:
    return dict(expect(http.get(f"/work-items/{key}"), 200))


def follow(key: str, minutes: int = 60) -> None:
    """What the work item waits for, and WHERE to decide: a re-authenticated person, in the console."""
    seen = None
    end = time.monotonic() + minutes * 60
    with client() as http:
        project = _project(http)
        while time.monotonic() < end:
            item = _item(http, key)
            waiting = []
            if item.get("pending_request"):
                request = item["pending_request"]
                waiting.append(f"{request['kind']}: {URL}/p/{PROJECT}/items/{item['id']}")
            for action in expect(
                http.get(f"/projects/{project['id']}/actions", params={"status": "pending_approval"}), 200
            ):
                if action.get("work_item_id") == item["id"]:
                    waiting.append(
                        f"action \"{action['title']}\" to decide: {URL}/p/{PROJECT}/actions/{action['id']}"
                    )
            state = (item["state"], tuple(waiting))
            if state != seen:
                print(f"[{time.strftime('%H:%M:%S')}] {item['state']}", *[f"\n  -> {a}" for a in waiting])
                seen = state
            if item["state"] in TERMINAL:
                return
            time.sleep(10)
    sys.exit(f"{key} did not get to the end in {minutes} min")


def move(key: str, date: str) -> None:
    """A changed date re-arms the timer of the action that waits for it."""
    with client() as http:
        item = _item(http, key)
        field = "start_date" if "start_date" in (item.get("fields") or {}) else "last_day"
        expect(http.patch(f"/work-items/{item['id']}", json={"fields": {field: date}}), 200)
    print(f"{key}: {field} = {date}")


def departure(arrival_key: str) -> None:
    """The departure of the person who arrived: their badge and laptop, read from the arrival's file."""
    with client() as http:
        project = _project(http)
        arrival_item = _item(http, arrival_key)
        fields = arrival_item["fields"]
        actions = expect(http.get(f"/projects/{project['id']}/actions"), 200)
        laptop = next(
            a for a in actions if a["work_item_id"] == arrival_item["id"] and a["kind"] == "onboarding.laptop"
        )
        serial = laptop["journal"][0]["result"]["serial"] if laptop.get("journal") else None
        if serial is None:
            serial = expect(http.get(f"/projects/{project['id']}/actions/{laptop['id']}"), 200)["journal"][0][
                "result"
            ]["serial"]
        body = {"title": f"Leaver: {fields['full_name']}", "labels": ["leaver"],
                "fields": {"full_name": fields["full_name"], "upn": fields["upn"],
                           "last_day": dt.date.today().isoformat(), "badge_uid": fields["badge_uid"],
                           "serial": serial, "address": fields["address"]}}  # fmt: skip
        item = expect(http.post(f"/projects/{project['id']}/work-items", json=body), 201)
    print(f"work item {item['tracker_key']} - departure today - {URL}/p/{PROJECT}/items/{item['id']}")


def verify(key: str) -> None:
    """A person's evidence file: each action, its decisions, its journal, and what was attested."""
    with client() as http:
        project = _project(http)
        item = _item(http, key)
        actions = [
            a
            for a in expect(http.get(f"/projects/{project['id']}/actions"), 200)
            if a["work_item_id"] == item["id"]
        ]
        failures = 0
        for summary in actions:
            action = expect(http.get(f"/projects/{project['id']}/actions/{summary['id']}"), 200)
            deciders = ", ".join(d["by"] for d in action["decisions"]) or "-"
            print(f"{action['status']:>16}  {action['kind']}  (decided by {deciders})")
            for effect in action["journal"]:
                print(f"{'':>18}{effect['key']}  {effect['effect']}  {effect['status']}")
            failures += action["status"] != "succeeded"
        timeline = expect(http.get(f"/work-items/{item['id']}/timeline"), 200)
        for entry in timeline:
            if "attested" in (entry.get("detail") or ""):
                print(f"proof: {entry['detail']} - {entry.get('actor')}")
    print(f"{key}: {item['state']}, {len(actions)} actions, {failures} without success")
    sys.exit(1 if failures or item["state"] not in TERMINAL else 0)


if __name__ == "__main__":
    command, *arguments = sys.argv[1:] or ["help"]
    if command == "prepare":
        prepare()
    elif command == "arrival":
        arrival(int(arguments[1]) if arguments[:1] == ["--in"] else -1)
    elif command == "follow":
        follow(arguments[0])
    elif command == "move":
        move(arguments[0], arguments[1])
    elif command == "departure":
        departure(arguments[0])
    elif command == "verify":
        verify(arguments[0])
    else:
        print(__doc__)
