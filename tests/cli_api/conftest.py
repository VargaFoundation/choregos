# SPDX-License-Identifier: Apache-2.0
"""The real API, started as a process, driven by the real CLI over HTTP (S67-03, S67-10).

The CLI's own tests fake the API with respx and check the bodies against `openapi.yaml`: they prove what the
CLI does with an answer, not that the API gives it. Here `uvicorn tranibor_api.main:app` runs on a local port
with `CHOREGOS_FAKES=1` and a throwaway SQLite database, exactly as `tools/e2e_live_api.py` starts it for the
console's live journeys; a person signs in as in development (`?as=<email>`), and `choregos` is invoked as a
subprocess, as a terminal would. What is faked is what a laptop does not have: Temporal, the tracker, the
model.
What is not faked: the HTTP server, the application, its routing, its guards, its database, the CLI.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

ADMIN = "admin@northwind.dev"
ORG = "northwind"


@dataclass
class Api:
    url: str
    database: Path
    home: Path

    def env(self, *, profile: str = "session", **extra: str) -> dict[str, str]:
        """The environment of a `choregos` process: its own home and profile, never the developer's."""
        env = {
            k: v for k, v in os.environ.items() if not k.startswith(("CHOREGOS_", "NO_COLOR", "FORCE_COLOR"))
        }
        env.update(
            HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"), COLUMNS="120", TERM="dumb"
        )
        env.update(
            CHOREGOS_API_URL=self.url, CHOREGOS_ORG=ORG, CHOREGOS_CONFIG=str(self.home / f"{profile}.json")
        )
        env.update(extra)
        return env

    def choregos(
        self, *args: str, profile: str = "session", stdin: str | None = None, **env: str
    ) -> subprocess.CompletedProcess[str]:
        """Run the CLI as a terminal would, with the credentials saved under `profile` (`session`: what
        `choregos login` saved; see `sign_in`)."""
        return subprocess.run(
            [sys.executable, "-m", "tranibor_cli.main", *args],
            env=self.env(profile=profile, **env),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def console(self) -> httpx.Client:
        """A browser's session of the administrator: the cookie the console would hold."""
        http = httpx.Client(base_url=self.url, timeout=30, follow_redirects=False)
        start = http.get("/api/v1/auth/login", params={"as": ADMIN})
        assert start.status_code == 307, start.text
        callback = http.get(start.headers["location"])
        assert callback.status_code == 307, callback.text
        assert http.cookies.get("choregos_session"), "no session cookie"
        return http


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def api(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Api]:
    root = tmp_path_factory.mktemp("cli-api")
    port = _free_port()
    database = root / "choregos.db"
    home = root / "home"
    home.mkdir()
    url = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items() if not k.startswith("CHOREGOS_")}
    env.update(
        CHOREGOS_ENV="test",
        CHOREGOS_FAKES="1",
        CHOREGOS_DATABASE_URL=f"sqlite+aiosqlite:///{database}",
        CHOREGOS_DEV_LOGIN_ENABLED="true",
        CHOREGOS_DEV_ADMIN_EMAILS=ADMIN,
        CHOREGOS_BOOTSTRAP_ORG=ORG,
        CHOREGOS_BOOTSTRAP_ORG_NAME="Northwind",
        CHOREGOS_BOOTSTRAP_ADMINS=ADMIN,
        CHOREGOS_PUBLIC_URL=url,
        CHOREGOS_SESSION_SECRET="cli-api-session-secret-" + "x" * 32,
        CHOREGOS_EGRESS_MODE="warn",
    )
    log = (root / "api.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tranibor_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 90
        while True:
            assert process.poll() is None, f"the API exited:\n{(root / 'api.log').read_text()[-3000:]}"
            assert time.monotonic() < deadline, (
                f"the API did not start:\n{(root / 'api.log').read_text()[-3000:]}"
            )
            try:
                if httpx.get(f"{url}/api/v1/edition", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        yield Api(url=url, database=database, home=home)
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


@pytest.fixture(scope="session")
def console(api: Api) -> Iterator[httpx.Client]:
    http = api.console()
    yield http
    http.close()


def project(console: httpx.Client, slug: str = "billing-api") -> str:
    """The id of the project `slug`, created once through the API as an administrator would."""
    found = console.get(f"/api/v1/orgs/{ORG}/projects")
    assert found.status_code == 200, found.text
    body = found.json()
    for existing in body["items"] if isinstance(body, dict) else body:
        if existing["slug"] == slug:
            return str(existing["id"])
    repo = {"url": f"https://github.com/{ORG}/{slug}.git", "default_branch": "main", "language": "python"}
    made = console.post(
        f"/api/v1/orgs/{ORG}/projects",
        json={"slug": slug, "name": slug, "config": {"slug": slug, "org": ORG, "repo": repo}},
    )
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def lay_run(
    api: Api,
    project_id: str,
    run_id: str,
    *,
    backend: str | None,
    status: str = "running",
    events: list[dict[str, Any]] | tuple[()] = (),
) -> None:
    """A run and its journal in the API's database. The API gives no route to create a run: the orchestrator
    does, and the runner then posts its journal to an internal route that needs the run's own token. What is
    laid down here is the rows those two write, read back by the real routes."""
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from tranibor_api.db.models import Run, RunEvent, WorkItem

    with Session(create_engine(f"sqlite:///{api.database}")) as session:
        item = WorkItem(project_id=project_id, tracker_key=f"ACME-{run_id}", title="A fix", state="planned")
        session.add(item)
        session.flush()
        session.add(
            Run(
                id=run_id,
                work_item_id=item.id,
                project_id=project_id,
                stage_role="implement",
                attempt=1,
                status=status,
                backend=backend,
                stage_input={},
            )
        )
        for event in events:
            ts = datetime.fromisoformat(
                str(event.get("ts") or "2026-11-10T09:00:00+00:00").replace("Z", "+00:00")
            )
            session.add(
                RunEvent(run_id=run_id, seq=event["seq"], type=event["type"], payload=event["payload"], ts=ts)
            )
        session.commit()


def sign_in(api: Api, console: httpx.Client, profile: str = "session") -> None:
    """`choregos login` against the API, as a person does: the CLI shows a code, the person approves it in
    the console (here, the administrator's cookie), and the CLI saves a session of that person's."""
    import re

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tranibor_cli.main",
            "login",
            "--api-url",
            api.url,
            "--org",
            ORG,
            "--no-browser",
        ],
        env=api.env(profile=profile),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    shown = ""
    code = None
    deadline = time.monotonic() + 30
    while code is None:
        assert time.monotonic() < deadline, f"no code shown:\n{shown}"
        line = process.stdout.readline()
        assert line or process.poll() is None, f"login ended early:\n{shown}"
        shown += line
        code = (re.search(r"shows the code (\S+?),? then approve", line) or [None, None])[1]
    code = re.sub(r"\x1b\[[0-9;]*m", "", code)
    approved = console.post(f"/api/v1/auth/device/requests/{code}/decision", json={"decision": "approve"})
    assert approved.status_code == 204, approved.text
    rest, _ = process.communicate(timeout=60)
    assert process.returncode == 0 and "signed in as" in rest, shown + rest


def save_token(api: Api, console: httpx.Client, profile: str = "token") -> None:
    """An API token of the administrator, saved with `choregos login --token` (what a script holds)."""
    made = console.post("/api/v1/me/tokens", json={"name": "script", "scopes": ["*"]})
    assert made.status_code in (200, 201), made.text
    secret = made.json()["token"]
    done = api.choregos("login", "--api-url", api.url, "--org", ORG, "--token", secret, profile=profile)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.fixture(scope="session")
def signed_in(api: Api, console: httpx.Client) -> Api:
    """The CLI holds a session of the administrator's, obtained by the device flow."""
    sign_in(api, console)
    return api


def extend_run(api: Api, run_id: str, events: list[dict[str, Any]], *, status: str | None = None) -> None:
    """What the runner posts while the run goes on: more journal lines, then the run's end."""
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from tranibor_api.db.models import Run, RunEvent

    with Session(create_engine(f"sqlite:///{api.database}")) as session:
        for event in events:
            ts = datetime.fromisoformat(
                str(event.get("ts") or "2026-11-10T09:01:00+00:00").replace("Z", "+00:00")
            )
            session.add(
                RunEvent(run_id=run_id, seq=event["seq"], type=event["type"], payload=event["payload"], ts=ts)
            )
        if status:
            run = session.get(Run, run_id)
            assert run is not None
            run.status = status
        session.commit()
