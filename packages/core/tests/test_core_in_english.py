"""The rest of the core is English (S45-08, ADR 0098), and every former name another package imports
still resolves to the English object."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]

SOURCES = tuple(
    f"packages/core/src/choregos_core/{name}"
    for name in (
        "__init__.py",
        "access.py",
        "admission.py",
        "secrets.py",
        "catalogue.py",
        "catalogues.py",
        "cron.py",
        "egress.py",
        "errors.py",
        "exit_codes.py",
        "hosted.py",
        "images.py",
        "llm_guardrail.py",
        "llm_spans.py",
        "mcp.py",
        "mcp_catalogue/",
        "memory_bindings.py",
        "memory_scope.py",
        "models.py",
        "network.py",
        "outbound_proxy.py",
        "prompt_loop.py",
        "quotas.py",
        "redaction.py",
        "telemetry.py",
        "web_search.py",
    )
)
TESTS = (
    "test_access",
    "test_backend_enabled",
    "test_catalogue",
    "test_cron",
    "test_docs_updated",
    "test_egress",
    "test_egress_network_bounds",
    "test_injection",
    "test_llm_guardrail",
    "test_llm_spans",
    "test_mcp_catalogue",
    "test_memory_bindings",
    "test_memory_scope",
    "test_models_resolution",
    "test_native_sidecars",
    "test_network",
    "test_numbers_measured",
    "test_procedures_format",
    "test_prompt_loop",
    "test_redaction",
    "test_secrets",
    "test_skill_catalogue",
    "test_telemetry",
    "test_web_search_bounds",
    "test_core_in_english",
)


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english_s45_08", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


def test_core_outside_the_dsl_has_no_french() -> None:
    checker = _checker()
    prefixes = (*SOURCES, *(f"packages/core/tests/{name}.py" for name in TESTS))
    try:
        found = checker.scan_tree(prefixes)
    except RuntimeError:  # an exported tree without .git: nothing to scan
        pytest.skip("not a git checkout")
    assert [str(f) for f in found] == []


def test_former_names_still_import() -> None:
    import choregos_core
    from choregos_core import acces, access, catalogue, exit_codes, mcp, secrets, sorties

    assert sorties.explication is exit_codes.explanation  # lang: fr-ok
    assert sorties.Exit is exit_codes.Exit
    assert acces.rapport_d_acces is access.access_report  # lang: fr-ok
    assert acces.Acces is access.AccessLine  # lang: fr-ok
    assert acces.RapportAcces is access.AccessReport  # lang: fr-ok
    assert choregos_core.rapport_d_acces is choregos_core.access_report  # lang: fr-ok
    assert choregos_core.Acces is access.AccessLine  # lang: fr-ok
    assert choregos_core.RapportAcces is access.AccessReport  # lang: fr-ok

    assert mcp.ERREUR_DE_SYNTAXE == mcp.PARSE_ERROR == -32700  # lang: fr-ok
    assert mcp.REQUETE_INVALIDE == mcp.INVALID_REQUEST == -32600  # lang: fr-ok
    assert mcp.METHODE_INCONNUE == mcp.METHOD_NOT_FOUND == -32601  # lang: fr-ok
    assert mcp.PARAMETRES_INVALIDES == mcp.INVALID_PARAMS == -32602  # lang: fr-ok
    assert mcp.ERREUR_INTERNE == mcp.INTERNAL_ERROR == -32603  # lang: fr-ok
    assert mcp.negocier is mcp.negotiate  # lang: fr-ok
    assert mcp.version_acceptable is mcp.acceptable_version  # lang: fr-ok
    assert mcp.erreur is mcp.error  # lang: fr-ok
    assert mcp.resultat_texte is mcp.text_result  # lang: fr-ok
    assert mcp.est_notification is mcp.is_notification  # lang: fr-ok
    assert mcp.defaut_du_message is mcp.message_defect  # lang: fr-ok

    assert catalogue.OutilCatalogue is catalogue.CatalogueTool  # lang: fr-ok
    assert catalogue.AppelHttp is catalogue.HttpCall  # lang: fr-ok
    assert catalogue.AppelMcp is catalogue.McpCall  # lang: fr-ok
    assert catalogue.OutilInconnuError is catalogue.UnknownToolError  # lang: fr-ok
    assert catalogue.ArgumentRefuseError is catalogue.ArgumentRefusedError  # lang: fr-ok
    assert catalogue.valider_les_arguments is catalogue.validate_arguments  # lang: fr-ok
    assert catalogue.construire_requete is catalogue.build_request  # lang: fr-ok
    assert catalogue.charger_catalogue is catalogue.load_catalogue  # lang: fr-ok
    assert catalogue.GABARIT is catalogue.PLACEHOLDER  # lang: fr-ok
    assert choregos_core.OutilCatalogue is catalogue.CatalogueTool  # lang: fr-ok
    assert choregos_core.charger_catalogue is catalogue.load_catalogue  # lang: fr-ok
    assert choregos_core.construire_requete is catalogue.build_request  # lang: fr-ok

    assert secrets.ReferenceInvalide is secrets.InvalidReference  # lang: fr-ok
    assert secrets.VariableRefusee is secrets.RefusedVariable  # lang: fr-ok
    assert secrets.PATTERN_PAR_DEFAUT == secrets.DEFAULT_PATTERN  # lang: fr-ok
    assert secrets.est_une_reference_du_magasin is secrets.is_store_reference  # lang: fr-ok
    assert secrets.verifier is secrets.check  # lang: fr-ok
    assert secrets.resoudre is secrets.resolve  # lang: fr-ok
    assert secrets.reinitialiser is secrets.reset  # lang: fr-ok


def test_former_keywords_still_work() -> None:
    from choregos_core.mcp import text_result

    assert text_result("boom", en_erreur=True)["isError"] is True  # lang: fr-ok
    assert text_result("ok", structure={"a": 1})["structuredContent"] == {"a": 1}  # lang: fr-ok
    assert text_result("ok", is_error=True, structured=[1])["structuredContent"] == [1]


def test_former_members_still_read() -> None:
    from choregos_core.access import AccessLine, AccessReport
    from choregos_core.catalogue import load_catalogue

    catalogue = load_catalogue(
        "tools:\n"
        "  - {name: some_tool, description: d, provider: p, groups: [hr], http: {url: 'https://example.test'}}\n"
    )
    assert catalogue.outils is catalogue.tools  # lang: fr-ok
    tool = catalogue.tools[0]
    assert catalogue.par_nom("some_tool") is tool  # lang: fr-ok
    assert tool.ouvert_a(["hr"]) is tool.open_to(["hr"]) is True  # lang: fr-ok
    assert tool.ouvert_a(["legal"]) is tool.open_to(["legal"]) is False  # lang: fr-ok

    line = AccessLine(nature="write", target="/x", requests=2, refusals=1, reasons=["why"])
    assert (line.cible, line.demandes, line.refus, line.motifs, line.refuse) == (  # lang: fr-ok
        "/x",
        2,
        1,
        ["why"],
        True,
    )
    report = AccessReport(lines=[line], events=3, inferred=1)
    assert report.acces is report.lines  # lang: fr-ok
    assert (report.evenements, report.deduites) == (3, 1)  # lang: fr-ok
    assert report.refuses == report.refused_lines == [line]  # lang: fr-ok
    assert report.par_nature("write") == report.by_nature("write") == [line]  # lang: fr-ok


def test_the_demo_catalogue_still_loads() -> None:
    """An operator's catalogue, written with the former key, keeps loading."""
    from choregos_core.catalogue import load_catalogue

    path = ROOT / "demo" / "outils" / "catalogue.yaml"  # lang: fr-ok
    if not path.exists():
        pytest.skip("no demo catalogue in this tree")
    assert load_catalogue(path.read_text(encoding="utf-8")).tools
