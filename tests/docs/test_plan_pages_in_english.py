# SPDX-License-Identifier: Apache-2.0
"""S45-35: the plan pages, the product review and the two READMEs are English, and link to English names.

`tools/check_english.py` counts French prose in every tracked file. This test runs its tree scan and
refuses any finding in the twelve pages, so a French paragraph cannot come back without the test
turning red. The old French file names are kept as one-line stubs (first line `# Moved`) so that
outside links still resolve; no page of this set may link to one of them.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[2]
PAGES = (
    "docs/plan/00-index.md",
    "docs/plan/01-architecture-and-contracts.md",
    "docs/plan/02-orchestrator-agents-runner.md",
    "docs/plan/03-connectors-templates-front.md",
    "docs/plan/04-models-costs-memory.md",
    "docs/plan/05-release-train-findings.md",
    "docs/plan/06-kubernetes-deployment.md",
    "docs/plan/07-delivery-parallelisation-conventions.md",
    "docs/plan/BLOCKERS.md",
    "docs/plan/PRODUCT-REVIEW-2026-10-07.md",
    "README.md",
    "apps/web/README.md",
)
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_english_for_plan_pages", ROOT / "tools" / "check_english.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_plan_pages_and_readmes_have_no_french() -> None:
    checker = _load_checker()
    findings = [
        f for f in checker.scan_tree(("docs/plan/", "README.md", "apps/web/README.md")) if f.path in PAGES
    ]
    assert findings == [], "\n".join(
        f"{f.path}:{f.line}: {f.reason}: {f.text.strip()[:100]}" for f in findings
    )


def test_every_page_exists() -> None:
    for page in PAGES:
        assert (ROOT / page).is_file(), page


def test_no_link_targets_a_french_stub() -> None:
    checked = 0
    for page in PAGES:
        path = ROOT / page
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for target in LINK.findall(line):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                file = (path.parent / target.split("#", 1)[0]).resolve()
                if not file.is_file():
                    continue
                checked += 1
                first = file.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
                assert first != ["# Moved"], f"{page}:{number} links the stub {target}"
    assert checked > 10  # the scan reads real links, not an empty set
