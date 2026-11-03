"""The registry, the extension loader, the trackers, executors, gateways and SCM are English
(S45-10, ADR 0098), and every former name still works: the module paths, the globals that tests
patch, and the extension loader that `apps/api` and the orchestrator worker still call."""

from __future__ import annotations

import importlib
import importlib.util
import pathlib
import re
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = "packages/adapters/src/choregos_adapters/"

#: What S45-10 owns, as `scan_tree` prefixes. `__init__.py` is checked apart, below: S46-01's MCP
#: parts live there.
OWNED = (
    f"{SRC}registry.py",
    f"{SRC}base.py",
    f"{SRC}errors.py",
    f"{SRC}tracker/",
    f"{SRC}executor/",
    f"{SRC}gateway/",
    f"{SRC}cd/",
    f"{SRC}ci/",
    f"{SRC}scm/",
    f"{SRC}github/",
    f"{SRC}http/",
    f"{SRC}a2a/",
)
OWNED_TESTS = (
    "_transport",
    "conftest",
    "test_a2a_card",
    "test_a2a_client",
    "test_aca",
    "test_agent_sandbox",
    "test_argocd",
    "test_direct_and_internal",
    "test_egress_transport",
    "test_extensions",
    "test_gateway_capabilities",
    "test_git_source_egress",
    "test_github_auth_and_client",
    "test_github_scm",
    "test_github_tracker",
    "test_gitlab",
    "test_hosted_executors",
    "test_image_builders",
    "test_jira",
    "test_job_rbac",
    "test_k8s_job",
    "test_local_docker",
    "test_log_archive",
    "test_openai_compatible_gateway",
    "test_pod_security",
    "test_project_team",
    "test_registry_defaults",
    "test_registry_in_english",
    "test_rest_client",
    "test_routing_executor",
    "test_run_gateway_key",
    "test_simulated_executor",
    "test_tekton",
    "test_connector_types",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_10", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_owned_adapters_have_no_french() -> None:
    checker = _checker()
    tests = tuple(f"packages/adapters/tests/{name}.py" for name in OWNED_TESTS)
    try:
        found = checker.scan_tree((*OWNED, *tests))
        in_init = checker.scan_tree((f"{SRC}__init__.py",))
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    findings = [str(f) for f in found]
    assert findings == []
    # `__init__.py` holds S46-01's MCP parts, which stay as they are: no declaration of the rest is French.
    declarations = [str(f) for f in in_init if f.kind == "identifier"]
    assert declarations == []


def test_patching_a_former_fake_global_patches_the_english_one(monkeypatch: pytest.MonkeyPatch) -> None:
    import choregos_adapters

    for former, current in (
        ("FAUX_ENTRA", "FAKE_ENTRA"),
        ("FAUX_MARKETDATA", "FAKE_MARKETDATA"),
        ("FAUX_WEB_SEARCH", "FAKE_WEB_SEARCH"),
    ):
        sentinel = object()
        monkeypatch.setattr(choregos_adapters, former, sentinel)
        assert getattr(choregos_adapters, current) is sentinel, f"{former} must reach {current}"
        assert getattr(choregos_adapters, former) is sentinel
    # What the factories read is the English global: a patch through the former name is seen by them.
    sentinel = object()
    monkeypatch.setattr(choregos_adapters, "FAUX_WEB_SEARCH", sentinel)
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    assert choregos_adapters.build("web_search", "fake") is sentinel


def test_patching_charger_les_greffons_patches_load_extensions(  # lang: fr-ok (the former name)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import choregos_adapters

    calls: list[str] = []
    monkeypatch.setattr(choregos_adapters, "charger_les_greffons", lambda: calls.append("loaded") or ["x"])
    assert choregos_adapters.load_extensions() == ["x"]
    assert calls == ["loaded"]
    # The group is the English constant, and its value is the entry-point group ADR 0093 keeps.
    assert choregos_adapters.GROUPE_DE_GREFFONS == choregos_adapters.EXTENSION_GROUP == "choregos.plugins"


def test_former_tracker_modules_still_import() -> None:
    from choregos_adapters.tracker import fields, internal
    from choregos_adapters.tracker.champs import nom_actuel, nom_present
    from choregos_adapters.tracker.interne import InternalTracker

    assert InternalTracker is internal.InternalTracker
    assert importlib.import_module("choregos_adapters.tracker.interne") is internal
    assert importlib.import_module("choregos_adapters.tracker.champs") is fields
    assert nom_actuel is fields.current_name and nom_present is fields.present_name
    # The former French names of the board's fields live in this one table, as data.
    assert fields.FORMER_NAMES == {"Cost (€)": "Coût (€)", "Size": "Taille", "Risk": "Risque"}  # lang: fr-ok
    assert nom_actuel("Taille") == "Size" and nom_present("Size", {"Taille"}) == "Taille"


def test_former_registry_and_base_names_still_resolve() -> None:
    from choregos_adapters import POLITIQUES, base, registry

    assert POLITIQUES is registry.POLICIES == ("allowed", "approval", "forbidden")
    assert registry.TYPES_PAR_DEFAUT is registry.DEFAULT_TYPES
    assert registry.type_par_defaut is registry.default_type
    assert registry.schema_d_entree is registry.build_input_schema
    assert registry.references_de_secret is registry.secret_references
    assert registry.configuration_resolue is registry.resolved_configuration
    assert base.CAPACITES_EXECUTEUR is base.EXECUTOR_CAPABILITIES
    assert registry.DEFAULT_TYPES["tracker"] == "internal"


def test_the_wire_keeps_its_former_names_for_the_tests_of_other_stories() -> None:
    from ._transport import Fil, Wire, no_wait, sans_attente

    assert Fil is Wire and sans_attente is no_wait
    wire = Wire()
    assert wire.requetes is wire.requests


def test_a_stored_connector_identifier_is_not_renamed() -> None:
    """Type ids and config fields live in `org_connectors` rows: the rename never touches them."""
    from choregos_adapters import available, spec_of

    assert {"internal", "jira", "github-issues", "gitlab-issues", "plugin"} <= set(available("tracker"))
    plugin = spec_of("tracker", "plugin")
    assert plugin is not None and plugin.config_schema is not None
    assert {"plugin", "server"} <= set(plugin.config_schema.get("properties", {}))


def test_the_english_group_is_the_one_the_loader_reads_by_default() -> None:
    from choregos_adapters import EXTENSION_GROUP, load_extensions

    # Declared in the source as the loader's default: renaming one without the other breaks discovery.
    source = (ROOT / SRC / "__init__.py").read_text(encoding="utf-8")
    assert re.search(r"def load_extensions\(group: str = EXTENSION_GROUP\)", source)
    assert EXTENSION_GROUP == "choregos.plugins"
    assert load_extensions("choregos.group.that.does.not.exist") == []
