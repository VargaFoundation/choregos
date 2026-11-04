# SPDX-License-Identifier: Apache-2.0
"""A fresh demonstration organisation, in English, recreated with one command (S21-29).

The product review of 2026-10-07 (point 11): the dev tenant's demo was made by hand, and its
projects - `billing`, `rh` - were born before the templates went English (ADR 0039). Their
workflows and the HR agents of the `varga` organisation therefore stay in French: nothing rewrites a
published workflow or an installed agent. A NEW organisation is born from today's templates.

    uv run python trials/demo/prepare_the_demo.py           # the organisation, the HR project, the dev project

What it creates, by replaying the scripts of the two trials:
- the organisation `TRIAL_DEMO_ORG` (default `demo`) - the caller becomes its administrator; in the
  community edition, which holds only one, the demo goes into the existing organisation, project `rh-demo`;
- the `rh` project (template `joiners-leavers`, its five connectors to the served fakes) -
  `trials/hr/scenario_hr.py prepare`;
- the `dev` project (template `github-software-delivery`) - `trials/dev-project/scenario_dev.py prepare`,
  only if `TRIAL_INSTALLATION` is set: it needs the tenant's GitHub App.

Variables: those of the two trials (`CHOREGOS_DEV_URL`, `CHOREGOS_DEV_TOKEN`...); `CHOREGOS_DEV_ORG`
is set here, from `TRIAL_DEMO_ORG`.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
from types import ModuleType

HERE = pathlib.Path(__file__).resolve().parent


def _trial(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, HERE.parent / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    org = os.environ.get("TRIAL_DEMO_ORG", "demo")
    os.environ["CHOREGOS_DEV_ORG"] = org
    hr = _trial("hr/scenario_hr.py", "scenario_hr")
    with hr.client() as http:
        known = {o["slug"] for o in hr.expect(http.get("/orgs"), 200)}
        if org not in known:
            response = http.post("/orgs", json={"slug": org, "name": org.capitalize()})
            if response.status_code == 409 and len(known) == 1:
                # The community edition holds a single organisation (ADR 0024): the demo goes into it,
                # under a project of its own (`TRIAL_HR_PROJECT`, default `rh-demo`).
                org = next(iter(known))
                os.environ["CHOREGOS_DEV_ORG"] = org
                os.environ.setdefault("TRIAL_HR_PROJECT", "rh-demo")  # lang: fr-ok
                hr = _trial("hr/scenario_hr.py", "scenario_hr")
                print(f"organisation: only one in the community edition - the demo goes into {org}")
            else:
                hr.expect(response, 201)
                print(f"organisation {org}: created")
        else:
            print(f"organisation {org}: already there")
    hr.prepare()
    if os.environ.get("TRIAL_INSTALLATION"):
        _trial("dev-project/scenario_dev.py", "scenario_dev").prepare()
    else:
        print("dev project: not created - TRIAL_INSTALLATION (the tenant's GitHub App) is not set")


if __name__ == "__main__":
    sys.exit(main())
