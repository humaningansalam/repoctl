from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
import subprocess

import pytest

from tools.repoctl import graph_workflow as workflow
from tools.repoctl.graph_model import GraphEdge, GraphNode, GraphSnapshot, file_id
from tools.repoctl.graph_store import materialize_graph
from tools.repoctl.repositories import RepoTarget


def _write(repo: Path, path: str, source: str) -> None:
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(source, encoding="utf-8")


def _target(repo: Path) -> RepoTarget:
    return RepoTarget("main", repo, "repos", "reserved")


def _snapshot(files: list[str], symbols=(), edges=()) -> GraphSnapshot:
    nodes = [GraphNode(file_id("main", path), "file", {"path": path}) for path in files]
    relations = list(edges)
    for sid, path, name in symbols:
        nodes.append(GraphNode(sid, "symbol", {}, {"provider": {"qualified_name": name}}))
        relations.append(GraphEdge("DEFINES", file_id("main", path), sid, "observed", "python_ast"))
    return GraphSnapshot({"id": "main", "path": "repos"}, [], {}, nodes, relations)


def _query(repo: Path, snapshot: GraphSnapshot, **selector) -> dict:
    return workflow.query_graph_workflow(
        snapshot, target=_target(repo),
        freshness=selector.pop("freshness", {"status": "current"}), **selector,
    )


def _build(tmp_path: Path, sources: dict[str, str]) -> tuple[Path, GraphSnapshot]:
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for path, source in sources.items():
        _write(repo, path, source)
    snapshot, problems, _ = materialize_graph(tmp_path, target=_target(repo))
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    return repo, snapshot


def test_route_navigation_uses_provider_calls_and_exact_http_requests(tmp_path) -> None:
    repo, snapshot = _build(tmp_path, {
        "api.py": "from service import run\n@router.post('/run')\ndef handler():\n    return run()\n",
        "service.py": "def run():\n    return 42\n",
        "consumer.py": "from service import run\ndef consume():\n    return run()\n",
        "tests/test_service.py": "from service import run\ndef test_run():\n    assert run() == 42\n",
        "tests/test_http.py": "def test_endpoint(client):\n    assert client.post('/run').status_code == 200\n",
        "tests/test_other.py": "def test_other(client):\n    assert client.post('/other').status_code == 200\n",
    })
    result = _query(repo, snapshot, route="/run")
    assert result["stages"]["entrypoints"][0]["path"] == "api.py"
    assert any(item["path"] == "service.py" and item["relation"] == "resolved"
               and item["evidence"] == "CALLS" for item in result["stages"]["implementation"])
    assert any(item["path"] == "consumer.py" and item["relation"] == "resolved"
               for item in result["stages"]["callers_or_dependents"])
    tests = {item["path"]: item for item in result["stages"]["tests"]}
    assert tests["tests/test_service.py"]["relation"] == "resolved"
    assert tests["tests/test_http.py"]["reference_kind"] == "python_literal_http_test"
    assert "tests/test_other.py" not in tests
    assert result["status"] == "found"


def test_dynamic_attribute_targets_remain_candidates_despite_factory_assignments(tmp_path) -> None:
    repo, snapshot = _build(tmp_path, {
        "api.py": (
            "from flask import current_app\n@router.post('/run')\ndef handler():\n"
            "    value = current_app.engine.run()\n"
            "    metrics.record('complete')\n"
            "    return jsonify({'value': value.to_dict()})\n"
        ),
        "engine.py": "class Engine:\n    def run(self):\n        return 42\n",
        "alternate.py": "class Alternate:\n    def run(self):\n        return 13\n",
        "factory.py": (
            "from engine import Engine\nfrom alternate import Alternate\nfrom api import router\n"
            "def create():\n    application = object()\n    application.engine = Engine()\n"
            "    application.register_blueprint(router)\n    application.engine = Alternate()\n"
            "    return application\n"
        ),
    })
    result = _query(repo, snapshot, route="/run")
    implementations = result["stages"]["implementation"]
    assert {(item["path"], item["relation"]) for item in implementations} == {
        ("engine.py", "candidate"), ("alternate.py", "candidate"),
    }
    assert all(link["relation"] == "candidate" for link in result["links"])
    assert any(item["path"] == "factory.py" and item["evidence"] == "IMPORTS_FILE"
               for item in result["stages"]["callers_or_dependents"])
    assert result["status"] == "partial"


def test_ambiguous_routes_require_an_exact_file(tmp_path) -> None:
    sources = {
        "first.py": "@router.get('/health')\ndef first():\n    return 'ok'\n",
        "second.py": "@router.get('/health')\ndef second():\n    return 'ok'\n",
    }
    for path, source in sources.items():
        _write(tmp_path, path, source)
    snapshot = _snapshot(list(sources))
    result = _query(tmp_path, snapshot, route="/health")
    assert result["status"] == "ambiguous"
    assert {item["path"] for item in result["entrypoints"]} == set(sources)
    selected = _query(tmp_path, snapshot, route="/health", in_file="second.py")
    assert [item["path"] for item in selected["stages"]["entrypoints"]] == ["second.py"]




@pytest.mark.parametrize(
    ("source", "reason", "files_read", "bytes_read"),
    [
        (b"\xff", "decode_error", 1, 1),
        (b"def invalid(:\n", "parse_error", 1, 14),
    ],
)
def test_uninspected_sources_are_partial_with_reasons_and_actual_read_counts(
    tmp_path, source, reason, files_read, bytes_read,
) -> None:
    (tmp_path / "source.py").write_bytes(source)
    result = _query(tmp_path, _snapshot(["source.py"]), route="/missing")
    assert result["status"] == "partial"
    assert result["stages"]["entrypoints"] == []
    completeness = result["completeness"]
    assert completeness["skipped_sources"] == [{"path": "source.py", "reason": reason}]
    assert completeness["files_scanned"] == files_read
    assert completeness["source_bytes_scanned"] == bytes_read
    assert completeness["source_scan_complete"] is False


def test_read_error_is_not_reported_as_an_empty_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "source.py"
    path.write_text("value = 1\n")
    original = Path.open

    def read_error(file, *args, **kwargs):
        if file == path:
            raise PermissionError("source cannot be read")
        return original(file, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read_error)
    result = _query(tmp_path, _snapshot(["source.py"]), route="/missing")
    assert result["status"] == "partial"
    assert result["completeness"]["skipped_sources"] == [{"path": "source.py", "reason": "read_error"}]
    assert result["completeness"]["files_scanned"] == 0
    assert result["completeness"]["source_bytes_scanned"] == 0




def test_successfully_read_empty_source_can_report_not_found(tmp_path) -> None:
    (tmp_path / "empty.py").write_bytes(b"")
    result = _query(tmp_path, _snapshot(["empty.py"]), route="/missing")
    assert result["status"] == "not_found"
    assert result["completeness"]["files_scanned"] == 1
    assert result["completeness"]["source_bytes_scanned"] == 0
    assert result["completeness"]["skipped_sources"] == []


def test_python_tree_is_shared_by_route_candidates_and_test_inspection(tmp_path, monkeypatch) -> None:
    sources = {
        "api.py": "@router.get('/run')\ndef handler():\n    return current_app.engine.run()\n",
        "engine.py": "class First:\n    def run(self):\n        return 1\nclass Second:\n    def run(self):\n        return 2\n",
        "tests/test_route.py": "def test_endpoint(client):\n    assert client.get('/run').status_code == 200\n",
    }
    for path, source in sources.items():
        _write(tmp_path, path, source)
    snapshot = _snapshot(list(sources), [
        ("handler", "api.py", "handler"),
        ("first", "engine.py", "First.run"),
        ("second", "engine.py", "Second.run"),
    ])
    original = workflow.ast.parse
    parsed = Counter()

    def count_parse(source, *args, **kwargs):
        parsed[source] += 1
        return original(source, *args, **kwargs)

    monkeypatch.setattr(workflow.ast, "parse", count_parse)
    result = _query(tmp_path, snapshot, route="/run")
    assert result["stages"]["implementation"][0]["relation"] == "candidate"
    assert result["stages"]["tests"][0]["reference_kind"] == "python_literal_http_test"
    assert parsed == Counter({source: 1 for source in sources.values()})
    assert result["completeness"]["files_scanned"] == len(sources)
    assert result["completeness"]["source_bytes_scanned"] == sum(len(s.encode()) for s in sources.values())


def test_dart_library_and_part_navigation_uses_structured_provider_relations(tmp_path) -> None:
    repo, snapshot = _build(tmp_path, {
        "pubspec.yaml": "name: app\n",
        "lib/authority.dart": "part 'authority_part.dart';\nclass Authority {}\n",
        "lib/authority_part.dart": "part of 'authority.dart';\nclass AuthorityPart {}\n",
        "test/authority_test.dart": "import 'package:app/authority.dart';\nvoid main() {}\n",
        "lib/comment.dart": "/*\nimport 'authority_part.dart';\n*/\nclass Unrelated {}\n",
    })
    result = _query(repo, snapshot, symbol="AuthorityPart", in_file="lib/authority_part.dart")
    assert any(item["path"] == "lib/authority.dart" and item["relation"] == "resolved"
               for item in result["stages"]["callers_or_dependents"])
    assert any(item["path"] == "test/authority_test.dart" and item["relation"] == "resolved"
               for item in result["stages"]["tests"])
    assert not any(item["path"] == "lib/comment.dart" for item in result["stages"]["callers_or_dependents"])


def test_stale_dart_imports_are_not_reconstructed_by_filename_regex(tmp_path) -> None:
    repo, snapshot = _build(tmp_path, {
        "lib/owner.dart": "class Owner {}\n",
        "lib/consumer.dart": "import 'owner.dart';\nclass Consumer {}\n",
    })
    _write(repo, "lib/consumer.dart", "/*\nimport 'owner.dart';\n*/\nclass Consumer {}\n")
    result = _query(
        repo, snapshot, symbol="Owner", in_file="lib/owner.dart",
        freshness={"status": "stale", "stale_paths": ["lib/consumer.dart"],
                   "changed_paths": ["lib/consumer.dart"]},
    )
    assert result["status"] == "partial"
    assert not any(item["path"] == "lib/consumer.dart" for item in result["stages"]["callers_or_dependents"])


def test_rust_module_navigation_keeps_owned_test_evidence(tmp_path) -> None:
    pytest.importorskip("tree_sitter_language_pack")
    repo, snapshot = _build(tmp_path, {
        "crates/daemon/src/main.rs": "mod audio_clock;\nfn main() {}\n",
        "crates/daemon/src/audio_clock.rs": (
            "pub struct AudioClock;\n#[cfg(test)]\nmod tests {\n    use super::*;\n"
            "    #[test]\n    fn drift() { let _clock = AudioClock; }\n}\n"
        ),
        "crates/other/src/lib.rs": "#[test]\nfn audio_clock_contract() { assert!(true); }\n",
    })
    result = _query(repo, snapshot, symbol="audio_clock")
    assert any(item["path"] == "crates/daemon/src/audio_clock.rs" and item["relation"] == "resolved"
               for item in result["stages"]["implementation"])
    assert any(item["path"] == "crates/daemon/src/audio_clock.rs"
               and item.get("reference_kind") == "rust_embedded_test_decl"
               for item in result["stages"]["tests"])
    assert not any(item["path"] == "crates/other/src/lib.rs" for item in result["stages"]["tests"])


def test_workflow_cli_discloses_provider_failure_without_demoting_local_relations(tmp_path, capsys) -> None:
    import json

    from tools.repoctl.cli import main

    (tmp_path / "docs/tasks").mkdir(parents=True)
    _build(tmp_path, {
        "service.py": "def run():\n    return 42\n",
        "tests/test_service.py": "from service import run\ndef test_run():\n    assert run() == 42\n",
        "broken.py": "def unfinished(:\n",
    })
    assert main([
        "--workspace-root", str(tmp_path), "graph", "workflow",
        "--repo-id", "main", "--symbol", "run", "--in-file", "service.py", "--json",
    ]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    coverage = data["completeness"]["provider_coverage"]["symbols"]
    assert "broken.py" in coverage["failed_paths"]
    assert coverage["status"] == "partial"
    result = data["result"]
    assert result["status"] == "found"
    assert any(item["path"] == "service.py" and item["relation"] == "resolved"
               for item in result["stages"]["implementation"])
    assert any(item["path"] == "tests/test_service.py" and item["relation"] == "resolved"
               for item in result["stages"]["tests"])

def test_handler_return_dependencies_follow_assignments_and_terminate_cycles() -> None:
    assignments = ["    response0 = manager.load()"]
    assignments.extend(f"    response{i} = response{i - 1}" for i in range(1, 180))
    source = "\n".join([
        "def handler():", *assignments,
        "    ignored = logger.record('observed')",
        "    return serialize(response179)",
    ])
    function = ast.parse(source).body[0]
    assert workflow.collect_route_value_flow(function) == {("load", "manager")}
    cycle = ast.parse("def handler():\n    first = second\n    second = first\n    return first\n").body[0]
    assert workflow.collect_route_value_flow(cycle) == set()
