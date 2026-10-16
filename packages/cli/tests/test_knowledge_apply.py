"""`choregos knowledge apply` (S39-18c): a manifest makes a knowledge base, idempotently."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
KB = f"{API}/api/v1/orgs/acme/knowledge"

MANIFEST = """apiVersion: choregos/v1
kind: KnowledgeBase
slug: acme
name: Acme
description: About Acme
serving_mode: index
sources:
  - name: website
    kind: web
    config: {url: "https://example.com/", exclude: ["/fr/*"]}
    interval_minutes: 1440
  - name: handbook
    kind: git
    config: {url: "https://github.com/acme/handbook.git", paths: ["docs/*.md"]}
uploads:
  - corpus/*.md
eval:
  - {question: "What does Acme sell?", expected: [offers.md]}
"""


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"api_url": API, "token": "chg_x", "org": "acme"}))
    import choregos_cli.client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", path)


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    (root / "corpus").mkdir(parents=True)
    (root / "corpus" / "offers.md").write_text("# Offers\n")
    (root / "corpus" / "about.md").write_text("# About\n")
    (root / "kb.yaml").write_text(MANIFEST)
    return root / "kb.yaml"


def _run(*args: str) -> Any:
    from choregos_cli.main import app

    return CliRunner().invoke(app, list(args))


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _existing(**override: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "slug": "acme", "name": "Acme", "description": "About Acme", "scope": "org", "projects": [],
        "groups": [], "serving_mode": "index",
        "sources": [
            {"id": "s1", "kind": "web", "name": "website", "connector": None, "interval_minutes": 1440,
             "config": {"url": "https://example.com/", "max_depth": 2, "exclude": ["/fr/*"]}},
            {"id": "s2", "kind": "git", "name": "handbook", "connector": None, "interval_minutes": 60,
             "config": {"url": "https://github.com/acme/handbook.git", "ref": "main",
                        "paths": ["docs/*.md"]}},
        ],
        "documents": [
            {"id": "d1", "filename": "offers.md", "source": "upload", "digest": _digest("# Offers\n")},
            {"id": "d2", "filename": "about.md", "source": "upload", "digest": _digest("# About\n")},
        ],
        "eval_set": [{"question": "What does Acme sell?", "expected": ["offers.md"]}],
    }  # fmt: skip
    return {**base, **override}


def _mutations(mock: respx.MockRouter) -> list[tuple[str, str]]:
    return [(c.request.method, c.request.url.path) for c in mock.calls if c.request.method != "GET"]


def test_a_first_apply_on_an_empty_org_creates_everything(manifest: Path) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(KB + "/acme").mock(return_value=Response(404, json={"detail": "not found"}))
        mock.post(KB).mock(return_value=Response(201, json={}))
        mock.post(KB + "/acme/sources").mock(return_value=Response(201, json={}))
        mock.post(KB + "/acme/documents").mock(return_value=Response(202, json={}))
        mock.put(KB + "/acme/eval").mock(return_value=Response(200, json={}))
        result = _run("knowledge", "apply", "-f", str(manifest))
        assert result.exit_code == 0, result.output
        assert _mutations(mock) == [
            ("POST", "/api/v1/orgs/acme/knowledge"),
            ("POST", "/api/v1/orgs/acme/knowledge/acme/sources"),
            ("POST", "/api/v1/orgs/acme/knowledge/acme/sources"),
            ("POST", "/api/v1/orgs/acme/knowledge/acme/documents"),
            ("POST", "/api/v1/orgs/acme/knowledge/acme/documents"),
            ("PUT", "/api/v1/orgs/acme/knowledge/acme/eval"),
        ]
        created = json.loads(mock.calls[1].request.content)
        assert created["slug"] == "acme" and created["serving_mode"] == "index"
    assert "+ source website" in result.output and "+ upload offers.md" in result.output


def test_a_second_apply_changes_nothing(manifest: Path) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(KB + "/acme").mock(return_value=Response(200, json=_existing()))
        result = _run("knowledge", "apply", "-f", str(manifest))
        assert result.exit_code == 0, result.output
        assert _mutations(mock) == []
    assert "= 6 unchanged" in result.output and "0 change" in result.output


def test_a_changed_corpus_file_replaces_its_document(manifest: Path) -> None:
    (manifest.parent / "corpus" / "offers.md").write_text("# Offers, revised\n")
    with respx.mock(assert_all_called=False) as mock:
        mock.get(KB + "/acme").mock(return_value=Response(200, json=_existing()))
        mock.delete(KB + "/acme/documents/d1").mock(return_value=Response(204))
        mock.post(KB + "/acme/documents").mock(return_value=Response(202, json={}))
        result = _run("knowledge", "apply", "-f", str(manifest))
        assert result.exit_code == 0, result.output
        assert _mutations(mock) == [
            ("DELETE", "/api/v1/orgs/acme/knowledge/acme/documents/d1"),
            ("POST", "/api/v1/orgs/acme/knowledge/acme/documents"),
        ]
    assert "~ upload offers.md" in result.output


def test_a_dry_run_prints_the_plan_and_calls_nothing(manifest: Path) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(KB + "/acme").mock(return_value=Response(404, json={"detail": "no"}))
        result = _run("knowledge", "apply", "-f", str(manifest), "--dry-run")
        assert result.exit_code == 0, result.output
        assert _mutations(mock) == []
    assert "+ knowledge base acme" in result.output and "dry run" in result.output


def test_prune_removes_what_the_manifest_does_not_declare(manifest: Path) -> None:
    extra = {"id": "s9", "kind": "web", "name": "old-site", "connector": None, "interval_minutes": 60,
             "config": {"url": "https://old.example.com/"}}  # fmt: skip
    existing = _existing()
    existing["sources"].append(extra)
    existing["documents"].append(
        {"id": "d9", "filename": "stale.md", "source": "upload", "digest": "sha256:0"}
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get(KB + "/acme").mock(return_value=Response(200, json=existing))
        mock.delete(KB + "/acme/sources/s9").mock(return_value=Response(204))
        mock.delete(KB + "/acme/documents/d9").mock(return_value=Response(204))
        kept = _run("knowledge", "apply", "-f", str(manifest))
        assert _mutations(mock) == [] and "use --prune" in kept.output
        pruned = _run("knowledge", "apply", "-f", str(manifest), "--prune")
        assert pruned.exit_code == 0, pruned.output
        assert sorted(_mutations(mock)) == [
            ("DELETE", "/api/v1/orgs/acme/knowledge/acme/documents/d9"),
            ("DELETE", "/api/v1/orgs/acme/knowledge/acme/sources/s9"),
        ]


def test_a_changed_source_is_patched(manifest: Path) -> None:
    existing = _existing()
    existing["sources"][0]["config"]["exclude"] = []
    with respx.mock(assert_all_called=False) as mock:
        mock.get(KB + "/acme").mock(return_value=Response(200, json=existing))
        mock.patch(KB + "/acme/sources/s1").mock(return_value=Response(200, json={}))
        result = _run("knowledge", "apply", "-f", str(manifest))
        assert result.exit_code == 0, result.output
        assert _mutations(mock) == [("PATCH", "/api/v1/orgs/acme/knowledge/acme/sources/s1")]


def test_a_manifest_that_is_wrong_fails_before_any_call(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(MANIFEST.replace('{url: "https://example.com/", exclude: ["/fr/*"]}', "{}"))
    with respx.mock(assert_all_called=False) as mock:
        result = _run("knowledge", "apply", "-f", str(bad))
        assert result.exit_code == 1
        assert mock.calls.call_count == 0
    assert "is not a valid knowledge base manifest" in " ".join(result.output.split())


def test_an_upload_pattern_that_matches_nothing_fails_before_any_call(tmp_path: Path) -> None:
    lonely = tmp_path / "kb.yaml"
    lonely.write_text(MANIFEST)
    with respx.mock(assert_all_called=False) as mock:
        result = _run("knowledge", "apply", "-f", str(lonely))
        assert result.exit_code == 1 and mock.calls.call_count == 0
    assert "matches no file" in " ".join(result.output.split())


def test_a_url_upload_is_downloaded_over_https_with_a_cap(tmp_path: Path) -> None:
    from choregos_cli.knowledge import ManifestError, collect_uploads, load_manifest

    path = tmp_path / "kb.yaml"
    path.write_text(
        MANIFEST.replace("  - corpus/*.md", "  - url: https://cdn.example.com/files/brochure.pdf")
    )
    manifest = load_manifest(path)
    ok = httpx.MockTransport(lambda request: Response(200, content=b"%PDF-1.4"))
    (upload,) = collect_uploads(manifest, tmp_path, transport=ok)
    assert upload.filename == "brochure.pdf" and upload.data == b"%PDF-1.4"
    huge = httpx.MockTransport(lambda request: Response(200, content=b"x" * (20 * 1024 * 1024 + 1)))
    with pytest.raises(ManifestError, match="larger than 20 MiB"):
        collect_uploads(manifest, tmp_path, transport=huge)
    path.write_text(MANIFEST.replace("  - corpus/*.md", "  - url: http://cdn.example.com/a.pdf"))
    with pytest.raises(ManifestError, match="https"):
        load_manifest(path)


def test_sync_and_eval_call_the_endpoints() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(KB + "/acme").mock(return_value=Response(200, json=_existing()))
        mock.post(KB + "/acme/sources/s1/sync").mock(return_value=Response(202, json={}))
        mock.post(KB + "/acme/sources/s2/sync").mock(return_value=Response(202, json={}))
        mock.post(KB + "/acme/eval/run").mock(
            return_value=Response(
                200,
                json={"eval_result": {"k": 5, "recall": 0.5, "mode": "hybrid", "cases": [
                    {"question": "q1", "expected": ["a"], "found": ["a"]},
                    {"question": "q2", "expected": ["b"], "found": []},
                ]}},
            )
        )  # fmt: skip
        synced = _run("knowledge", "sync", "acme")
        assert synced.exit_code == 0 and synced.output.count("sync requested") == 2
        evaluated = _run("knowledge", "eval", "acme")
        assert evaluated.exit_code == 0
    assert "recall@5 = 0.5" in evaluated.output and "✗ q2" in evaluated.output


def test_the_shipped_example_is_a_valid_manifest() -> None:
    from choregos_cli.knowledge import collect_uploads, load_manifest

    example = Path(__file__).parents[3] / "essai" / "knowledge" / "example-kb.yaml"
    manifest = load_manifest(example)
    assert {u.filename for u in collect_uploads(manifest, example.parent)} == {"offers.md", "about.md"}
