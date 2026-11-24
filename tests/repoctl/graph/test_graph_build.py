from __future__ import annotations

import copy
import json
import hashlib
from pathlib import Path

from tools.repoctl.code_index import CodeIndexEntry
from tools.repoctl.cli import main
from tools.repoctl.graph import build_graph
from tools.repoctl.graph_model import canonical_json, file_id, import_ref_id
from tools.repoctl.graph_store import load_materialized_graph, materialize_graph
from tools.repoctl.repositories import RepoTarget, require_repo_target
from tools.repoctl.io import Problem
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.repository.test_repositories import init_repo, write_settings



def _snapshot(payload: dict) -> dict:
    return payload["data"]["snapshot"]


def _materialize(root: Path) -> None:
    snapshot, problems, _meta = materialize_graph(root, target=require_repo_target(root, repo_id="main"))
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

def test_graph_build_direct_repo_uses_main(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.py").write_text("import hashlib\n\ndef run():\n    return hashlib.sha256(b'x').hexdigest()\n", encoding="utf-8")
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "build", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    snapshot = _snapshot(payload)
    assert snapshot["repository"] == {"id": "main", "path": "repos", "identity_source": "reserved"}
    assert any(node["id"] == "repo:main" and node["kind"] == "repository" for node in snapshot["nodes"])
    assert any(node["id"] == file_id("main", "app.py") and node["kind"] == "file" for node in snapshot["nodes"])




def test_graph_build_json_is_summary_first_without_full(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "build", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "snapshot" not in payload["data"]
    summary = payload["data"]["summary"]
    assert summary["repository"] == {"id": "main", "path": "repos", "identity_source": "reserved"}
    assert summary["node_count"] >= 2
    assert summary["edge_count"] >= 1
    assert summary["snapshot_digest"].startswith("sha256:")
    assert payload["data"]["materialization"]["code_index"]["changed_paths"] == ["app.py"]
    assert "provider_coverage" not in payload["data"]
    assert "semantic_providers" not in summary


def test_graph_build_excludes_generated_dependency_dirs_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (repo / ".venv/lib/python3.11/site-packages/pkg").mkdir(parents=True)
    (repo / ".venv/lib/python3.11/site-packages/pkg/noise.py").write_text("def vendor():\n    return 1\n", encoding="utf-8")
    (repo / ".next/server/chunks").mkdir(parents=True)
    (repo / ".next/server/chunks/noise.js").write_text("export const generated = true\n", encoding="utf-8")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__/app.cpython-311.pyc").write_bytes(b"pyc")
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    paths = {node["identity"].get("path") for node in snapshot["nodes"] if node["kind"] == "file"}
    assert "app.py" in paths
    assert ".venv/lib/python3.11/site-packages/pkg/noise.py" not in paths
    assert ".next/server/chunks/noise.js" not in paths
    assert "__pycache__/app.cpython-311.pyc" not in paths

def test_graph_build_configured_multi_requires_repo_id(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "build", "--full", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"

def test_graph_build_configured_multi_includes_only_selected_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    (web / "app.py").write_text("import requests\n", encoding="utf-8")
    (api / "app.py").write_text("import urllib.request\n", encoding="utf-8")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "build", "--repo-id", "web", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert snapshot["repository"]["id"] == "web"
    assert any(node["id"] == file_id("web", "app.py") for node in snapshot["nodes"])
    assert not any(node["id"].startswith("repo:api:") for node in snapshot["nodes"])

def test_graph_build_unconfigured_collection_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "build", "--repo-id", "web", "--full", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_not_found"


def test_graph_snapshot_digest_is_stable_after_incremental_round_trip(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "lib").mkdir()
    (repo / "lib/main.dart").write_text(
        "import 'z.dart';\n"
        "import 'a.dart';\n",
        encoding="utf-8",
    )
    changed = repo / "lib/changed.dart"
    original = "const value = 1;\n"
    changed.write_text(original, encoding="utf-8")
    target = require_repo_target(tmp_path, repo_id="main")
    first, first_problems, _ = materialize_graph(tmp_path, target=target)
    changed.write_text(original + "// incremental validation\n", encoding="utf-8")
    updated, updated_problems, _ = materialize_graph(tmp_path, target=target)
    changed.write_text(original, encoding="utf-8")
    restored, restored_problems, _ = materialize_graph(tmp_path, target=target)

    assert first is not None
    assert updated is not None
    assert restored is not None
    assert not [problem for problem in [*first_problems, *updated_problems, *restored_problems] if problem.severity == "error"]
    assert restored.snapshot_digest == first.snapshot_digest
    assert canonical_json(restored.to_dict()) == canonical_json(first.to_dict())


def test_graph_materialization_updates_only_changed_python_dependents(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "a.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("from a import target\n", encoding="utf-8")
    (repo / "c.py").write_text("from b import target\n\ndef caller():\n    return target()\n", encoding="utf-8")
    (repo / "unrelated.py").write_text("def unrelated():\n    return 1\n", encoding="utf-8")
    target = require_repo_target(tmp_path, repo_id="main")
    first, problems, _meta = materialize_graph(tmp_path, target=target)
    assert first is not None
    assert not [problem for problem in problems if problem.severity == "error"]

    (repo / "a.py").write_text("def target():\n    return 2\n", encoding="utf-8")

    second, problems, meta = materialize_graph(tmp_path, target=target)

    assert second is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    assert meta["materialization"]["updated_paths"] == {"python_ast": ["a.py", "b.py", "c.py"]}
    assert any(
        node.kind == "symbol" and node.facts.get("provider", {}).get("qualified_name") == "unrelated"
        for node in second.nodes
    )
    names = {
        node.id: node.facts.get("provider", {}).get("qualified_name")
        for node in second.nodes
        if node.kind == "symbol"
    }
    assert any(
        edge.kind == "CALLS"
        and names.get(edge.from_id) == "caller"
        and names.get(edge.to_id) == "target"
        for edge in second.edges
    )

    from tools.repoctl import graph_store

    monkeypatch.setitem(
        graph_store.PROVIDER_INPUT_VERSIONS,
        "python_ast",
        graph_store.PROVIDER_INPUT_VERSIONS["python_ast"] + 1,
    )

    third, problems, meta = materialize_graph(tmp_path, target=target)

    assert third is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    assert meta["materialization"]["updated_paths"] == {
        "python_ast": ["a.py", "b.py", "c.py", "unrelated.py"]
    }

    (repo / "a.py").unlink()

    fourth, problems, meta = materialize_graph(tmp_path, target=target)

    assert fourth is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    assert meta["materialization"]["updated_paths"] == {
        "python_ast": ["b.py", "c.py", "unrelated.py"]
    }
    assert not any(edge.kind == "CALLS" for edge in fourth.edges)
    provider = json.loads((tmp_path / ".repoctl-state/graph/main/providers/python_ast.json").read_text(encoding="utf-8"))
    assert not any(item.get("path") == "a.py" for item in provider["tool"]["exported_callables"])


def test_graph_materialization_invalidates_importers_when_module_identity_becomes_ambiguous(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "pyproject.toml").write_text(
        '[tool.setuptools]\npackage-dir = {"" = "src"}\n\n[tool.setuptools.packages.find]\nwhere = ["src"]\n',
        encoding="utf-8",
    )
    (repo / "src/pkg").mkdir(parents=True)
    (repo / "src/pkg/__init__.py").write_text("", encoding="utf-8")
    (repo / "src/pkg/mod.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    (repo / "consumer.py").write_text("from pkg.mod import target\n\ndef caller():\n    return target()\n", encoding="utf-8")
    target = require_repo_target(tmp_path, repo_id="main")
    first, problems, _meta = materialize_graph(tmp_path, target=target)
    assert first is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    assert any(edge.kind == "CALLS" for edge in first.edges)

    (repo / "pkg").mkdir()
    (repo / "pkg/__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg/mod.py").write_text("def target():\n    return 2\n", encoding="utf-8")

    second, problems, meta = materialize_graph(tmp_path, target=target)

    assert second is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    assert meta["materialization"]["updated_paths"] == {
        "python_ast": ["consumer.py", "pkg/__init__.py", "pkg/mod.py", "src/pkg/__init__.py", "src/pkg/mod.py"]
    }
    assert not any(edge.kind == "CALLS" for edge in second.edges)
    assert not any(
        edge.kind == "IMPORTS_FILE"
        and edge.from_id == file_id("main", "consumer.py")
        for edge in second.edges
    )


def test_graph_materialization_reuses_unchanged_inputs(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    target = require_repo_target(tmp_path, repo_id="main")
    first, problems, _meta = materialize_graph(tmp_path, target=target)
    assert first is not None
    assert not [problem for problem in problems if problem.severity == "error"]

    second, problems, meta = materialize_graph(tmp_path, target=target)

    assert second is not None
    assert second.snapshot_digest == first.snapshot_digest
    assert not [problem for problem in problems if problem.severity == "error"]
    assert meta["materialization"]["status"] == "reused"
    assert meta["materialization"]["code_index"]["changed_paths"] == []

    mismatched_target = RepoTarget("main", repo, "repos/renamed", "pinned")
    loaded, load_problems, load_meta = load_materialized_graph(tmp_path, target=mismatched_target)
    assert loaded is None
    assert [problem.code for problem in load_problems] == ["graph_materialization_repository_mismatch"]
    assert load_meta["materialization"]["status"] == "repository_mismatch"

    provider_path = tmp_path / ".repoctl-state/graph/main/providers/python_ast.json"
    provider_text = provider_path.read_text(encoding="utf-8")
    provider_path.write_text("{not-json\n", encoding="utf-8")
    invalid, invalid_problems, invalid_meta = materialize_graph(tmp_path, target=target)
    assert invalid is None
    assert [problem.code for problem in invalid_problems] == ["graph_materialization_invalid"]
    assert invalid_meta["materialization"]["status"] == "invalid"
    assert provider_path.read_text(encoding="utf-8") == "{not-json\n"
    provider_path.write_bytes(b"\xff")
    unreadable, read_problems, read_meta = materialize_graph(tmp_path, target=target)
    assert unreadable is None
    assert [problem.code for problem in read_problems] == ["graph_materialization_unavailable"]
    assert read_meta["materialization"]["status"] == "unavailable"
    assert provider_path.read_bytes() == b"\xff"
    provider_path.write_text(provider_text, encoding="utf-8")


def test_graph_materialization_rebuilds_relations_when_structured_input_version_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "Dockerfile").write_text("FROM scratch\nCOPY app.py /app/app.py\n", encoding="utf-8")
    (repo / "compose.yml").write_text(
        "services:\n  app:\n    build:\n      context: .\n      dockerfile: Dockerfile\n",
        encoding="utf-8",
    )
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    target = require_repo_target(tmp_path, repo_id="main")

    first, problems, _meta = materialize_graph(tmp_path, target=target)

    assert first is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    manifest_path = tmp_path / ".repoctl-state/graph/main/manifest.json"
    first_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    from tools.repoctl import graph_store

    monkeypatch.setattr(
        graph_store,
        "STRUCTURED_RELATION_INPUT_VERSION",
        first_manifest["structured_relation_input_version"] + 1,
    )

    second, problems, meta = materialize_graph(tmp_path, target=target)

    assert second is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    assert any(
        edge.kind == "USES_FILE"
        and edge.from_id == file_id("main", "Dockerfile")
        and edge.to_id == file_id("main", "app.py")
        for edge in second.edges
    )
    assert meta["materialization"]["status"] == "updated"
    assert meta["materialization"]["updated_providers"] == []
    assert meta["materialization"]["code_index"]["changed_paths"] == []
    second_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert second_manifest["structured_relation_input_version"] == first_manifest["structured_relation_input_version"] + 1
    assert second_manifest["input_digest"] != first_manifest["input_digest"]



def test_graph_index_truncation_fails(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    target = require_repo_target(tmp_path, repo_id="main")

    def truncated_index(*args, **kwargs):
        return [], [], {"summary": {"truncated": True, "total": 2, "returned": 1, "parse_error": 0}}

    monkeypatch.setattr("tools.repoctl.graph.build_code_index", truncated_index)

    snapshot, problems, _meta = build_graph(tmp_path, target=target)

    assert snapshot is None
    assert problems[0].code == "graph_index_truncated"

def test_graph_build_keeps_snapshot_with_code_index_warning(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    target = require_repo_target(tmp_path, repo_id="main")

    def warning_index(*args, **kwargs):
        return (
            [
                CodeIndexEntry(path='app.py', workspace_path='repos/app.py', language='python', classification='included', symbols=[], imports=[], calls=[])
            ],
            [Problem("warning", "index_warning", "non-fatal index warning", "repos/app.py")],
            {"summary": {"truncated": False, "parse_error": 0}},
        )

    monkeypatch.setattr("tools.repoctl.graph.build_code_index", warning_index)

    snapshot, problems, _meta = build_graph(tmp_path, target=target)

    assert snapshot is not None
    assert problems[0].severity == "warning"
    assert any(node.id == file_id("main", "app.py") for node in snapshot.nodes)

def test_graph_parse_error_keeps_file_node_and_marks_completeness(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert snapshot["completeness"]["code_facts_complete"] is False
    assert snapshot["completeness"]["parse_error_count"] == 1
    file_node = next(node for node in snapshot["nodes"] if node["id"] == file_id("main", "bad.py"))
    assert file_node["facts"]["index"]["parse_status"] == "parse_error"

def test_graph_id_encoding_avoids_collisions() -> None:
    assert file_id("main", "a/b") != file_id("main", "a%2Fb")
    assert import_ref_id("main", "app.ts", "typescript", "a:b") != import_ref_id("main", "app.ts", "typescript", "a/b")
    assert import_ref_id("main", "one/app.ts", "typescript", "axios") != import_ref_id("main", "two/app.ts", "typescript", "axios")

def test_graph_python_ast_provider_adds_symbol_and_anchor_nodes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "service.py").write_text(
        "class TokenService:\n"
        "    def issue(self):\n"
        "        return 'token'\n\n"
        "def helper():\n"
        "    return TokenService()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert "symbol" in snapshot["capabilities"]
    assert "anchor" in snapshot["capabilities"]
    symbols = [node for node in snapshot["nodes"] if node["kind"] == "symbol"]
    assert {node["facts"]["provider"]["qualified_name"] for node in symbols} == {"TokenService", "TokenService.issue", "helper"}
    assert all(node["identity"]["provider"] == "python_ast" for node in symbols)
    assert any(node["kind"] == "anchor" and node["identity"]["path"] == "service.py" for node in snapshot["nodes"])
    assert any(edge["kind"] == "DEFINES" and edge["from"] == file_id("main", "service.py") for edge in snapshot["edges"])
    assert any(edge["kind"] == "ANCHORS" and edge["assertion"] == "resolved" and edge["source"] == "python_ast" for edge in snapshot["edges"])



def test_graph_python_ast_provider_distinguishes_nested_function_from_method(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "nested.py").write_text(
        "class Service:\n"
        "    def method(self):\n"
        "        def inner():\n"
        "            return 1\n"
        "        return inner()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    kinds = {node["facts"]["provider"]["qualified_name"]: node["facts"]["provider"]["kind"] for node in snapshot["nodes"] if node["kind"] == "symbol"}
    assert kinds["Service.method"] == "method"
    assert kinds["Service.method.inner"] == "function"

def test_typescript_import_edges_use_parsed_declarations_and_refresh(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "real.ts").write_text("export function run() { return 1; }\n")
    (repo / "fake.ts").write_text("export const fake = true;\n")
    (repo / "app.ts").write_text(
        "import { run } from './real';\n"
        "export { value } from './future';\n"
        "// import { fake } from './fake';\n"
        "/* require('./fake'); */\n"
        "const fixture = \"import { fake } from './fake';\";\n"
        "const template = `require('./fake')`;\n"
        "function shadowed(require: (name: string) => unknown) { return require('./fake'); }\n"
        "export const result = run();\n"
    )
    target = require_repo_target(tmp_path, repo_id="main")
    snapshot, problems, _meta = materialize_graph(tmp_path, target=target)
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    declarations = [
        node.identity["raw_import"] for node in snapshot.nodes
        if node.kind == "import_ref" and node.identity["importer_path"] == "app.ts"
    ]
    assert set(declarations) == {"./real", "./future"}
    assert any(edge.kind == "IMPORTS_FILE" and edge.to_id == file_id("main", "real.ts") for edge in snapshot.edges)
    assert not any(edge.kind == "IMPORTS_FILE" and edge.to_id == file_id("main", "fake.ts") for edge in snapshot.edges)

    (repo / "future.ts").write_text("export const value = 2;\n")
    refreshed, problems, _meta = materialize_graph(tmp_path, target=target)
    assert refreshed is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    assert any(edge.kind == "IMPORTS_FILE" and edge.to_id == file_id("main", "future.ts") for edge in refreshed.edges)

    (repo / "app.ts").write_text("// import { run } from './real';\nexport const result = 3;\n")
    changed, problems, _meta = materialize_graph(tmp_path, target=target)
    assert changed is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    assert not any(node.kind == "import_ref" and node.identity["importer_path"] == "app.ts" for node in changed.nodes)


def test_graph_reports_unreadable_provider_input_without_materializing(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "pubspec.yaml").write_text("name: sample\n")
    package_config = repo / ".dart_tool/package_config.json"
    package_config.parent.mkdir()
    package_config.write_text('{"configVersion":2,"packages":[]}\n')
    read_bytes = Path.read_bytes
    def read(path: Path) -> bytes:
        if path == package_config:
            raise PermissionError("package config is unreadable")
        return read_bytes(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    snapshot, problems, meta = materialize_graph(tmp_path, target=require_repo_target(tmp_path, repo_id="main"))
    assert snapshot is None
    assert [problem.code for problem in problems] == ["graph_input_unreadable"]
    assert meta["materialization"]["status"] == "unavailable"
    assert not (tmp_path / ".repoctl-state/graph/main/snapshot.json").exists()


def test_knowledge_citation_does_not_declare_code_applicability(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    for name in ("applies.py", "cited.py"):
        (repo / name).write_text("value = 1\n")
    monkeypatch.setattr("tools.repoctl.graph.knowledge_records_for_graph", lambda *args, **kwargs: ([
        {
            "id": "K-1", "claim": "A saved decision", "status": "active",
            "applies_to": {"paths": ["applies.py"]},
            "source_refs": [{"path": "repos/cited.py", "content_sha256": "sha256:" + "a" * 64}],
        }
    ], []))
    snapshot, problems, _meta = build_graph(tmp_path, target=require_repo_target(tmp_path, repo_id="main"))
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    applies = [edge.to_id for edge in snapshot.edges if edge.kind == "KNOWLEDGE_APPLIES_TO"]
    assert applies == [file_id("main", "applies.py")]
    provenance = [edge for edge in snapshot.edges if edge.kind == "KNOWLEDGE_SOURCED_FROM"]
    assert len(provenance) == 1
    assert provenance[0].assertion == "recorded"


def test_structural_incremental_build_preserves_cross_file_context(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    sources = {
        "go/main.go": "package queue\nfunc Dispatch() { Run() }\n",
        "go/run.go": "package queue\nfunc Run() {}\n",
        "main.rs": "mod clock;\nfn main() { clock::tick(); }\n",
        "clock.rs": "pub fn tick() {}\n",
    }
    for path, source in sources.items():
        source_path = repo / path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source, encoding="utf-8")
    target = require_repo_target(tmp_path, repo_id="main")
    original, original_problems, _ = materialize_graph(tmp_path, target=target)
    assert original is not None and not original_problems
    assert len([edge for edge in original.edges if edge.kind == "CALLS"]) == 2

    (repo / "go/run.go").write_text("// shifted declaration\n" + sources["go/run.go"], encoding="utf-8")
    updated, update_problems, _ = materialize_graph(tmp_path, target=target)
    rebuilt, rebuild_problems, _ = materialize_graph(tmp_path, target=target, rebuild=True)

    assert updated is not None and rebuilt is not None
    assert not update_problems and not rebuild_problems
    assert len([edge for edge in updated.edges if edge.kind == "CALLS"]) == 2
    assert updated.to_dict() == rebuilt.to_dict()

    (repo / "placeholder.txt").write_text("excluded source\n", encoding="utf-8")
    for path in sources:
        source_path = repo / path
        source_path.unlink()
        source_path.symlink_to(repo / "placeholder.txt")
    empty, empty_problems, _ = materialize_graph(tmp_path, target=target)
    clean, clean_problems, _ = materialize_graph(tmp_path, target=target, rebuild=True)

    assert empty is not None and clean is not None
    assert not empty_problems and not clean_problems
    assert not [edge for edge in empty.edges if edge.kind in {"CALLS", "IMPORTS_FILE"}]
    assert empty.to_dict() == clean.to_dict()
