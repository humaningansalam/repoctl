from __future__ import annotations

from pathlib import Path

import pytest

from tools.repoctl.graph import build_graph, query_graph
from tools.repoctl.graph_model import file_id
from tools.repoctl.io import RepoctlError
from tools.repoctl.repositories import require_repo_target
from tools.repoctl.task_history import (
    read_task_history,
    lookup_task_history,
    search_task_history,
)
from tests.repoctl.repository.test_repositories import init_repo


def archive(
    root: Path,
    *,
    identity: str = "T-20261120120000Z-01234567",
    title: str = "결제중복방지",
    repo_id: str = "main",
    suffix: str = "done",
) -> Path:
    path = root / "docs/archive/tasks" / f"{identity}--{suffix}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nid: {identity}\ntitle: "{title}"\nstatus: done\nrepo_id: "{repo_id}"\ncompleted_at: 2026-11-20T12:00:00Z\n---\n\n## Goal\n\nPreserve the final result without an index.\n\n## Discovery\n\n- Chosen files: `repos/app.py`\n- Notes: declared scope\n\n## Result\n\nDone.\n'
        + " ".join(f"a{number:04d}" for number in range(200)),
        encoding="utf-8",
    )
    return path


def test_history_search_reads_unicode_title_goal_and_terms_without_vocabulary_cutoff(
    tmp_path: Path,
) -> None:
    archive(tmp_path)
    for query in ("결제중복방지", "final result", "a0199"):
        matches, summary, problems = search_task_history(
            tmp_path, "main", query_terms=(query,)
        )
        assert not problems and len(matches) == 1
        assert summary["scanned_task_count"] == 1
    assert not (tmp_path / ".repoctl-state").exists()


def test_history_lookup_accepts_current_task_ids_and_keeps_exact_text(
    tmp_path: Path,
) -> None:
    path = archive(tmp_path)
    task = lookup_task_history(tmp_path, "main", "T-20261120120000Z-01234567")
    assert task is not None and task.text.encode("utf-8") == path.read_bytes()
    assert task.completed_at == "2026-11-20T12:00:00Z"
    assert lookup_task_history(tmp_path, "other", task.task_id) is None


def test_history_result_limit_does_not_drop_searchable_tail(tmp_path: Path) -> None:
    for number in range(7):
        archive(tmp_path, identity=f"T-20261120120000Z-{number:08x}")
    matches, summary, _ = search_task_history(
        tmp_path, "main", query_terms=("결제",), limit=7
    )
    assert len(matches) == 7 and not summary["truncated"]


def test_duplicate_task_identity_is_not_silently_selected(tmp_path: Path) -> None:
    archive(tmp_path, suffix="one")
    duplicate = archive(tmp_path, suffix="two")
    duplicate.rename(duplicate.with_name("human-plan.md"))
    with pytest.raises(RepoctlError) as error:
        lookup_task_history(tmp_path, "main", "T-20261120120000Z-01234567")
    assert error.value.code == "duplicate_task_id"
    assert lookup_task_history(tmp_path, "main", "docs/archive/tasks/human-plan.md") is not None


def test_graph_task_scope_remains_declared_and_archive_is_document_source(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path / "repos")
    (tmp_path / "repos/app.py").write_text("def run():\n    return 1\n")
    path = archive(tmp_path)
    task = lookup_task_history(tmp_path, "main", "T-20261120120000Z-01234567")
    snapshot, problems, _ = build_graph(
        tmp_path, target=require_repo_target(tmp_path), task_inputs=(task,)
    )
    assert snapshot is not None and not [
        problem for problem in problems if problem.severity == "error"
    ]
    references = [
        edge for edge in snapshot.edges if edge.kind == "TASK_REFERENCES_FILE"
    ]
    assert len(references) == 1 and references[0].assertion == "declared"
    assert references[0].to_id == file_id("main", "app.py")
    assert any(edge.kind == "TASK_DOCUMENTED_BY" for edge in snapshot.edges)
    assert not any(node.kind == "change_event" for node in snapshot.nodes)
    assert not any(
        edge.kind in {"TASK_CHANGED_FILE", "TASK_VERIFIED_BY"}
        for edge in snapshot.edges
    )


def test_exact_task_lookup_reads_only_selected_body_once(tmp_path: Path, monkeypatch) -> None:
    from contextlib import contextmanager

    generated = archive(tmp_path)
    selected = generated.with_name("previous-plan.md")
    generated.rename(selected)
    archive(tmp_path, identity="T-20261120120001Z-01234567")
    original_open = Path.open
    body_reads: list[Path] = []

    @contextmanager
    def observe_open(file, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        with original_open(file, *args, **kwargs) as stream:
            if mode == "r":
                body_reads.append(file)
            yield stream

    monkeypatch.setattr(Path, "open", observe_open)
    task = lookup_task_history(tmp_path, "main", "T-20261120120000Z-01234567")
    assert task is not None and task.title == "결제중복방지"
    assert task.path == selected.relative_to(tmp_path).as_posix()
    assert body_reads == [selected]


def test_terminal_status_is_history_even_before_document_archival(
    tmp_path: Path,
) -> None:
    archived = archive(tmp_path)
    current = tmp_path / "docs/tasks" / archived.name
    current.parent.mkdir(parents=True)
    archived.rename(current)
    entries, problems = read_task_history(tmp_path, repo_id="main")
    assert not problems and len(entries) == 1
    assert entries[0].path == current.relative_to(tmp_path).as_posix()
    assert lookup_task_history(tmp_path, "main", entries[0].task_id) == entries[0]
    active = current.with_name("T-20261120120002Z--active.md")
    active.write_text(
        current.read_text()
        .replace("status: done", "status: doing")
        .replace(entries[0].task_id, "T-20261120120002Z")
    )
    assert len(read_task_history(tmp_path, repo_id="main")[0]) == 1


def test_history_uses_the_resolved_document_source_when_disk_changes(tmp_path: Path, monkeypatch) -> None:
    from tools.repoctl import task_history

    path = archive(tmp_path)
    before = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(before)
    concurrent = before.replace(b"status: done", b"status: canceled") + b"\r\nNew author note.\r\n"
    original_resolve = task_history.resolve_task

    def select_then_edit(root, reference):
        task = original_resolve(root, reference)
        path.write_bytes(concurrent)
        return task

    monkeypatch.setattr(task_history, "resolve_task", select_then_edit)
    entry = lookup_task_history(tmp_path, "main", "T-20261120120000Z-01234567")
    assert entry is not None
    assert entry.status == "done"
    assert entry.text.encode() == before
    assert path.read_bytes() == concurrent
