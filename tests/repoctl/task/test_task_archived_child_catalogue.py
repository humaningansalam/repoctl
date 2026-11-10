from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.repoctl.task_lifecycle_helpers import (
    add_task,
    commit_all,
    init_committed_product_repo,
    task_text,
    write_workspace,
)
from tools.repoctl.cli import main
from tools.repoctl.completion_catalogue import (
    CompletionCatalogueUnavailable,
    CompletionCatalogueUnavailableReason,
    completion_catalogue_paths,
    ingest_completion_catalogue_tail,
    lookup_completion_exact,
    search_completion_history,
)
from tools.repoctl.tasks import archive_locator_path


PARENT_ID = "T-20260609184046Z"
CHILD_ID = "T-20260609184047Z"


@dataclass(frozen=True)
class ArchivedChild:
    live_path: str
    artifact: Path
    receipt: Path
    event: Path
    locator: Path
    completed_task_bytes: bytes
    receipt_bytes: bytes
    event_bytes: bytes


def _cli(capsys, *args: str, expected: int = 0) -> dict:
    status = main([*args, "--json"])
    result = json.loads(capsys.readouterr().out)
    assert status == expected, json.dumps(result, ensure_ascii=False)
    return result


def _finish_child_then_parent(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    checkpointed: bool = False,
) -> ArchivedChild:
    write_workspace(root)
    repo = root / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    add_task(root, f"{PARENT_ID}--parent.md", task_text(PARENT_ID))
    child_path = add_task(
        root,
        f"{CHILD_ID}--child.md",
        task_text(CHILD_ID, parent=PARENT_ID)
        .replace('area: ""', 'area: "backend"')
        .replace('repo_id: ""', 'repo_id: "main"'),
    )
    (root / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n"
        f"- docs/tasks/{PARENT_ID}--parent.md\n"
        f"- docs/tasks/{CHILD_ID}--child.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)

    _cli(capsys, "task", "start", PARENT_ID)
    _cli(capsys, "task", "start", CHILD_ID)
    _cli(capsys, "task", "discovery", "add", CHILD_ID, "--chosen", "repos/app.py")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    commit_all(repo, "Complete the child change")
    finished = _cli(capsys, "task", "finish", CHILD_ID, "--use-committed-diff")

    live_path = child_path.relative_to(root).as_posix()
    assert finished["data"]["archived"] is False
    assert finished["data"]["new_path"] == live_path
    receipt_path = root / finished["data"]["completion_receipt"]
    paths = completion_catalogue_paths(root, "main")
    event_paths = list(paths.events_directory.glob("*.json"))
    assert len(event_paths) == 1
    receipt_bytes = receipt_path.read_bytes()
    event_bytes = event_paths[0].read_bytes()
    completed_task_bytes = child_path.read_bytes()
    assert json.loads(receipt_bytes)["task_path_at_completion"] == live_path
    assert json.loads(event_bytes)["artifact_path"] == live_path

    if checkpointed:
        assert ingest_completion_catalogue_tail(root, "main").ingested_count == 1
    else:
        assert not paths.catalogue.exists()

    parent = _cli(capsys, "task", "finish", PARENT_ID)
    assert parent["data"]["archived"] is True
    archived = ArchivedChild(
        live_path=live_path,
        artifact=root / "docs/archive/tasks" / child_path.name,
        receipt=receipt_path,
        event=event_paths[0],
        locator=archive_locator_path(root, CHILD_ID),
        completed_task_bytes=completed_task_bytes,
        receipt_bytes=receipt_bytes,
        event_bytes=event_bytes,
    )
    assert not child_path.exists()
    assert json.loads(archived.locator.read_bytes())["task_path"] == archived.artifact.relative_to(root).as_posix()
    _assert_original_authorities(archived)
    return archived


def _assert_original_authorities(child: ArchivedChild) -> None:
    assert child.receipt.read_bytes() == child.receipt_bytes
    assert child.event.read_bytes() == child.event_bytes
    assert child.artifact.read_bytes() == child.completed_task_bytes


@pytest.mark.parametrize("checkpointed", [False, True], ids=["pending-tail", "checkpointed"])
@pytest.mark.parametrize("reader", ["audit", "exact", "search"])
def test_parent_archive_preserves_child_catalogue_read_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    checkpointed: bool,
    reader: str,
) -> None:
    child = _finish_child_then_parent(tmp_path, monkeypatch, capsys, checkpointed=checkpointed)

    if reader == "audit":
        audited = _cli(capsys, "check", "--audit-history")
        assert audited["ok"] is True
        assert audited["problems"] == []
        assert {
            "repo_id": "main",
            "status": "audited",
            "event_count": 1,
            "last_sequence": 1,
            "source_checked": True,
        } in audited["data"]["completion_history"]["audited_catalogues"]
    elif reader == "exact":
        record = lookup_completion_exact(tmp_path, "main", CHILD_ID)
        assert record is not None
        assert record.task_id == CHILD_ID
        assert record.artifact_path == child.live_path
        assert record.receipt == json.loads(child.receipt_bytes)
        assert record.event == json.loads(child.event_bytes)
    else:
        result = search_completion_history(tmp_path, "main", query_terms=[], task_ids=[CHILD_ID])
        assert len(result.records) == 1
        match = result.records[0]
        assert match.exact_task is True
        assert match.record.task_id == CHILD_ID
        assert match.record.event == json.loads(child.event_bytes)
        assert match.receipt_text.encode() == child.receipt_bytes
        assert match.artifact_text.encode() == child.completed_task_bytes

    _assert_original_authorities(child)


@pytest.mark.parametrize("damage", ["missing-locator", "conflicting-locator", "tampered-artifact"])
def test_archived_child_catalogue_rejects_unproved_path_or_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    damage: str,
) -> None:
    child = _finish_child_then_parent(tmp_path, monkeypatch, capsys, checkpointed=True)
    if damage == "missing-locator":
        child.locator.unlink()
    elif damage == "conflicting-locator":
        locator = json.loads(child.locator.read_bytes())
        locator["task_path"] = f"docs/archive/tasks/{CHILD_ID}--different.md"
        child.locator.write_text(json.dumps(locator) + "\n", encoding="utf-8")
    else:
        child.artifact.write_bytes(child.completed_task_bytes + b"\nUnrecorded completion evidence.\n")
    damaged_artifact = child.artifact.read_bytes()
    damaged_locator = child.locator.read_bytes() if child.locator.exists() else None

    audited = _cli(capsys, "check", "--audit-history", expected=1)
    assert audited["ok"] is False
    expected_check_code = (
        "invalid_completion_receipt"
        if damage == "tampered-artifact"
        else CompletionCatalogueUnavailableReason.SOURCE_MISMATCH.value
    )
    assert expected_check_code in {problem["code"] for problem in audited["problems"]}

    expected_reason = (
        CompletionCatalogueUnavailableReason.SOURCE_AUDIT_FAILED
        if damage == "tampered-artifact"
        else CompletionCatalogueUnavailableReason.SOURCE_MISMATCH
    )
    with pytest.raises(CompletionCatalogueUnavailable) as exact:
        lookup_completion_exact(tmp_path, "main", CHILD_ID)
    assert exact.value.reason == expected_reason
    with pytest.raises(CompletionCatalogueUnavailable) as search:
        search_completion_history(tmp_path, "main", query_terms=[], task_ids=[CHILD_ID])
    assert search.value.reason == expected_reason

    assert child.receipt.read_bytes() == child.receipt_bytes
    assert child.event.read_bytes() == child.event_bytes
    assert child.artifact.read_bytes() == damaged_artifact
    assert (child.locator.read_bytes() if child.locator.exists() else None) == damaged_locator


def test_explicit_rebuild_can_record_archived_path_for_live_completion_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    child = _finish_child_then_parent(tmp_path, monkeypatch, capsys)

    # Rebuild is exercised only as an explicit operation in this disposable fixture.
    _cli(capsys, "history", "rebuild", "--repo-id", "main")

    audited = _cli(capsys, "check", "--audit-history")
    assert audited["ok"] is True
    assert audited["problems"] == []
    record = lookup_completion_exact(tmp_path, "main", CHILD_ID)
    assert record is not None
    assert record.artifact_path == child.artifact.relative_to(tmp_path).as_posix()
    assert record.receipt["task_path_at_completion"] == child.live_path
    result = search_completion_history(tmp_path, "main", query_terms=[], task_ids=[CHILD_ID])
    assert len(result.records) == 1
    assert result.records[0].record.artifact_path == record.artifact_path
    assert result.records[0].artifact_text.encode() == child.completed_task_bytes
    assert child.receipt.read_bytes() == child.receipt_bytes
    assert child.artifact.read_bytes() == child.completed_task_bytes
