from __future__ import annotations

from pathlib import Path

from tools.repoctl.context import build_graph_source_evidence
from tools.repoctl.graph_store import materialize_graph
from tools.repoctl.repositories import require_repo_target
from tests.repoctl.repository.test_repositories import init_repo
from tests.repoctl.test_task_history import archive


def test_cold_context_search_finds_source_without_metadata_or_graph_cache(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.py").write_text("def validate_token():\n    return True\n")
    result, problems, _ = build_graph_source_evidence(
        tmp_path, target=require_repo_target(tmp_path), query="validate_token"
    )
    assert result is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    assert any(
        candidate.source_ref.path == "repos/app.py" for candidate in result.evidence
    )
    assert not (repo / ".repometa").exists()
    assert not (tmp_path / ".repoctl-state/graph").exists()


def test_explicit_history_reads_task_text_without_claiming_code_changes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.py").write_text("def unrelated_operation():\n    return True\n")
    source = archive(tmp_path)
    result, problems, _ = build_graph_source_evidence(
        tmp_path,
        target=require_repo_target(tmp_path),
        query="결제중복방지",
        mode="past_decision",
    )
    assert result is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    history = result.groups["related_history"]
    assert (
        history
        and history[0]["source_ref"]["path"] == source.relative_to(tmp_path).as_posix()
    )
    assert not result.groups["likely_change_surface"]
    assert not (tmp_path / ".repoctl-state").exists()


def test_graph_refresh_observes_git_ignore_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.py").write_text("def useful_operation():\n    return True\n")
    target = require_repo_target(tmp_path)
    first, problems, _ = materialize_graph(tmp_path, target=target)
    assert first is not None and not [
        problem for problem in problems if problem.severity == "error"
    ]
    assert any(node.kind == "symbol" for node in first.nodes)
    (repo / ".gitignore").write_text("app.py\n")
    second, problems, _ = materialize_graph(tmp_path, target=target)
    assert second is not None and not [
        problem for problem in problems if problem.severity == "error"
    ]
    assert not any(node.identity.get("path") == "app.py" for node in second.nodes)
