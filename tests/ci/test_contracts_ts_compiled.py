"""The generated TypeScript contract is compiled where it changes (S65-27, issue #1607).

`make contracts-check` compares the generated text with what is committed and never compiles it, and the
two applications typed by the contract import only the modules they use. A generated entry point that did
not compile (nine names exported twice, TS2308) stayed green that way. The console's `typecheck` now
compiles `packages/contracts/ts` as its own project, and the `web` job, which runs on the generated types,
runs that script: a name generated twice in a module turns that job red in the PR that generates it.
The package has no root entry any more (nothing imported it); it is read by `/schemas` and `/api`.
"""

from __future__ import annotations

import json
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
WEB = ROOT / "apps" / "web"
TS = ROOT / "packages" / "contracts" / "ts"


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _filters() -> dict:
    (step,) = [s for s in _workflow()["jobs"]["changes"]["steps"] if s.get("id") == "filter"]
    return yaml.safe_load(step["with"]["filters"])


def test_the_console_typecheck_compiles_the_contract_as_its_own_project() -> None:
    script = json.loads((WEB / "package.json").read_text(encoding="utf-8"))["scripts"]["typecheck"]
    commands = [c.strip() for c in script.split("&&")]
    assert "tsc -p ../../packages/contracts/ts" in commands
    tsconfig = json.loads((TS / "tsconfig.json").read_text(encoding="utf-8"))
    assert tsconfig["include"] == ["*.ts"]
    assert tsconfig["compilerOptions"]["noEmit"] is True


def test_the_web_job_runs_that_typecheck_on_a_change_of_the_generated_types() -> None:
    assert "packages/contracts/ts/**" in _filters()["web"]
    runs = [s["run"] for s in _workflow()["jobs"]["web"]["steps"] if "run" in s]
    assert "pnpm typecheck" in runs
    assert "web" in _workflow()["jobs"]["ci-ok"]["needs"]


def test_the_package_has_no_root_entry_that_would_re_export_both_modules() -> None:
    assert not (TS / "index.ts").exists()
    manifest = json.loads((TS / "package.json").read_text(encoding="utf-8"))
    assert manifest["exports"] == {"./schemas": "./schemas.ts", "./api": "./api.ts"}
    assert "main" not in manifest
    assert "types" not in manifest
    web_paths = json.loads((WEB / "tsconfig.json").read_text(encoding="utf-8"))["compilerOptions"]["paths"]
    assert "@choregos/contracts" not in web_paths
    assert web_paths["@choregos/contracts/*"] == ["../../packages/contracts/ts/*"]
