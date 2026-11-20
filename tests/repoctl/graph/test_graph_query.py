from __future__ import annotations

import json
from pathlib import Path

from tests.repoctl.graph.test_graph_build import _materialize
from tools.repoctl.cli import main
from tools.repoctl.graph_model import digest_data, file_id, import_ref_id
from tools.repoctl.repositories import require_repo_target
from tests.repoctl.io_audit import reject_directory_enumeration
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.repository.test_repositories import init_repo



def test_graph_query_file_returns_typed_subgraph(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "package.json").write_text('{"name":"graph-query-fixture"}\n', encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src/app.py").write_text("import hashlib\n\n\ndef api_error():\n    return 'root'\n", encoding="utf-8")
    (repo / "repos/src").mkdir(parents=True)
    (repo / "repos/src/app.py").write_text("def nested_app():\n    return 'nested'\n", encoding="utf-8")
    (repo / "repos/only.py").write_text("def only_nested():\n    return 'nested'\n", encoding="utf-8")
    _materialize(tmp_path)
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))
    target = require_repo_target(tmp_path, repo_id="main")
    monkeypatch.setattr("tools.repoctl.repositories.require_repo_target", lambda root, repo_id=None: target)
    with reject_directory_enumeration(
        monkeypatch,
        repo / "src",
        repo / "repos",
        repo / "package.json",
    ) as product_reads:
        assert main(["graph", "query", "--file", "src/app.py", "--json"]) == 0

        payload = json.loads(capsys.readouterr().out)
        result = payload["data"]["result"]
        assert result["matches"][0]["component_ids"] == [
            "component:package.json:.:graph-query-fixture"
        ]

        assert main(["graph", "query", "--file", "repos/src/app.py", "--json"]) == 1
        ambiguous_path = json.loads(capsys.readouterr().out)
        ambiguous_result = ambiguous_path["data"]["result"]
        assert ambiguous_path["problems"][0]["code"] == "graph_query_ambiguous_path"
        assert [item["path"] for item in ambiguous_result["candidates"]] == ["repos/src/app.py", "src/app.py"]
        assert main(["graph", "query", "--file", "repos/only.py", "--json"]) == 0
        exact_repo_path = json.loads(capsys.readouterr().out)["data"]["result"]
        assert exact_repo_path["query"] == {"type": "file", "path": "repos/only.py"}

        assert main(["graph", "query", "--file", "src/app.py", "--json"]) == 0

        repeated_payload = json.loads(capsys.readouterr().out)
        result = repeated_payload["data"]["result"]
        assert result["query"] == {"type": "file", "path": "src/app.py"}
        assert any(node["id"] == file_id("main", "src/app.py") for node in result["nodes"])
        assert any(edge["kind"] == "CONTAINS" and edge["to"] == file_id("main", "src/app.py") for edge in result["edges"])

        assert main(["graph", "query", "--symbol", "api_error", "--in-file", "../outside.py", "--json"]) == 1
        invalid_in_file = json.loads(capsys.readouterr().out)
        assert invalid_in_file["problems"][0]["code"] == "graph_query_invalid_path"

        assert main(["graph", "query", "--symbol", "api_error", "--in-file", "repos/src/app.py", "--json"]) == 1
        ambiguous_in_file = json.loads(capsys.readouterr().out)
        assert ambiguous_in_file["data"]["query_status"] == "ambiguous"
        assert [item["path"] for item in ambiguous_in_file["data"]["result"]["candidates"]] == ["repos/src/app.py", "src/app.py"]

        assert main(["graph", "query", "--symbol", "api_error", "--in-file", "missing/app.py", "--json"]) == 0
        missing_in_file = json.loads(capsys.readouterr().out)
        assert missing_in_file["data"]["query_status"] == "not_found"
        assert {item["path"] for item in missing_in_file["data"]["result"]["candidates"]} == {"repos/src/app.py", "src/app.py"}

    assert product_reads == []

    provider_path = tmp_path / ".repoctl-state/graph/main/providers/python_ast.json"
    removed_provider_path = provider_path.with_suffix(".removed")
    provider_path.rename(removed_provider_path)
    assert main(["graph", "query", "--file", "src/app.py", "--json"]) == 0
    incomplete = json.loads(capsys.readouterr().out)
    assert incomplete["data"]["materialization"]["status"] == "materialized_degraded"
    assert incomplete["data"]["materialization"]["components"]["relations"] == "available"
    assert incomplete["data"]["materialization"]["components"]["provider_artifacts"] == "unavailable"
    assert any(warning["code"] == "graph_provider_artifacts_unavailable" for warning in incomplete["warnings"])

    removed_provider_path.rename(provider_path)
    provider_data = json.loads(provider_path.read_text(encoding="utf-8"))
    provider_data["symbols"] = None
    provider_path.write_text(json.dumps(provider_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path = tmp_path / ".repoctl-state/graph/main/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provider_result_digests"]["python_ast"] = digest_data(provider_data)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["graph", "query", "--file", "src/app.py", "--json"]) == 0
    invalid_materialization = json.loads(capsys.readouterr().out)
    assert invalid_materialization["data"]["materialization"]["status"] == "materialized_degraded"
    assert invalid_materialization["data"]["query_status"] == "found"
    assert any(warning["code"] == "graph_provider_artifacts_unavailable" for warning in invalid_materialization["warnings"])




def test_graph_query_import_returns_declaring_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "frontend").mkdir()
    (repo / "frontend/app.ts").write_text("import axios from 'axios';\n", encoding="utf-8")
    _materialize(tmp_path)
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "query", "--import", "axios", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert result["query"] == {"type": "import", "raw_import": "axios"}
    assert any(node["id"] == import_ref_id("main", "frontend/app.ts", "typescript", "axios") for node in result["nodes"])
    assert any(node["id"] == file_id("main", "frontend/app.ts") for node in result["nodes"])
    declaration = next(edge for edge in result["edges"] if edge["kind"] == "DECLARES_IMPORT")
    assert declaration["assertion"] == "observed"
    assert declaration["source"] == "typescript_compiler"


def test_graph_query_requires_exactly_one_selector(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    _materialize(tmp_path)
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "query", "--json"]) == 1
    missing = json.loads(capsys.readouterr().out)
    assert missing["problems"][0]["code"] == "graph_query_selector_required"

    assert main(["graph", "query", "--file", "app.py", "--symbol", "run", "--json"]) == 1
    ambiguous = json.loads(capsys.readouterr().out)
    assert ambiguous["problems"][0]["code"] == "graph_query_selector_ambiguous"

    assert main(["graph", "query", "--file", "missing.py", "--json"]) == 0
    missing = json.loads(capsys.readouterr().out)
    assert missing["ok"] is True
    assert missing["data"]["query_status"] == "not_found"
    assert missing["data"]["completeness"]["status"] in {"complete", "partial"}
    assert missing["data"]["result"]["candidates"] == []

    assert main(["graph", "query", "--impact-symbol", "missing_owner", "--depth", "2", "--json"]) == 0
    missing_impact = json.loads(capsys.readouterr().out)
    expected_selector = {"type": "impact_symbol", "symbol": "missing_owner", "depth": 2}
    assert missing_impact["data"]["query_status"] == "not_found"
    assert missing_impact["data"]["result"]["query"] == expected_selector

    assert main(["graph", "query", "--file", "missing/app.py", "--json"]) == 0
    same_name = json.loads(capsys.readouterr().out)
    assert same_name["data"]["result"]["candidates"][0]["path"] == "app.py"




def test_graph_query_reports_snapshot_freshness(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    _materialize(tmp_path)
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "query", "--file", "app.py", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["freshness"]["status"] == "stale"
    assert payload["data"]["freshness"]["knowledge_input_changed"] is False
    assert payload["data"]["freshness"]["changed_paths"] == ["app.py"]
    assert any(warning["code"] == "graph_snapshot_stale" for warning in payload["warnings"])


def test_graph_call_query_reports_defined_but_missing_provider_as_unavailable(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.ts").write_text("export function run() { return 1; }\n", encoding="utf-8")
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr("tools.repoctl.graph_typescript_provider._find_compiler", lambda repo, entries: (None, ""))
    monkeypatch.setattr("tools.repoctl.graph_typescript_provider._bundled_compiler", lambda root: (None, ""))
    _materialize(tmp_path)

    assert main(["graph", "query", "--callers-of", "run", "--in-file", "app.ts", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["query_status"] == "unavailable"
    assert payload["data"]["result"]["query_status"] == "unavailable"
    assert payload["data"]["completeness"]["status"] == "partial"
    assert any(warning["code"] == "graph_query_unavailable" for warning in payload["warnings"])


def test_graph_shell_only_repo_reports_provider_coverage_as_unsupported(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "run.sh").write_text("#!/bin/sh\nrun_task() { echo ok; }\n", encoding="utf-8")
    _materialize(tmp_path)
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "query", "--symbol", "run_task", "--json"]) == 0

    compact = json.loads(capsys.readouterr().out)
    assert compact["data"]["query_status"] == "unsupported"

    assert main(["graph", "query", "--symbol", "run_task", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    completeness = payload["data"]["completeness"]
    assert payload["data"]["query_status"] == "unsupported"
    assert completeness["capabilities"]["symbols"] == "unsupported"
    assert completeness["capabilities"]["calls"] == "unsupported"
    assert completeness["provider_coverage"]["symbols"]["unsupported_paths"] == ["run.sh"]


def test_graph_relations_are_independent_of_source_index(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    _materialize(tmp_path)
    database = tmp_path / ".repoctl-state/graph/main/source-index.sqlite3"
    database.rename(database.with_suffix(".removed"))
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["graph", "query", "--file", "app.py", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["query_status"] == "found"
    assert result["data"]["materialization"]["components"] == {
        "relations": "available",
        "provider_artifacts": "available",
    }
    assert result["data"]["materialization"]["status"] == "materialized"
