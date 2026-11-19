from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.repoctl.task_lifecycle_helpers import add_task, init_repo, task_text, write_workspace
from tools.repoctl import cli
from tools.repoctl.io import RepoctlError
from tools.repoctl.markdown import parse_frontmatter
from tools.repoctl.tasks import finish_task


def run_json(root: Path, capsys, *args: str) -> tuple[int, dict]:
    result = cli.main(["--workspace-root", str(root), "--json", *args])
    return result, json.loads(capsys.readouterr().out)


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()
            and not path.is_relative_to(root / ".repoctl-state/locks")}


def test_cli_create_start_and_finish_in_minimal_workspace(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    code, created = run_json(tmp_path, capsys, "task", "create", "한글 제목 그대로", "--area", "product research", "--start")
    assert code == 0
    path = tmp_path / created["data"]["path"]
    frontmatter, original_body = parse_frontmatter(path.read_text(encoding="utf-8"))
    assert frontmatter["title"] == "한글 제목 그대로"
    assert frontmatter["area"] == "product research"
    assert frontmatter["status"] == "doing"
    assert frontmatter["started_at"]
    assert not (tmp_path / "docs/BOARD.md").exists()

    code, finished = run_json(tmp_path, capsys, "task", "finish", created["data"]["task_id"])
    assert code == 0
    assert not path.exists()
    archive = tmp_path / finished["data"]["path"]
    final_frontmatter, final_body = parse_frontmatter(archive.read_text(encoding="utf-8"))
    assert final_frontmatter["status"] == "done"
    assert final_frontmatter["completed_at"]
    assert final_body == original_body
    assert set(snapshot(tmp_path)) == {str(archive.relative_to(tmp_path))}


def test_cli_parent_and_child_finish_independently(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    parent = add_task(tmp_path, "parent.md", task_text("parent", status="doing"))
    child = add_task(tmp_path, "child.md", task_text("child", status="doing", parent="parent"))
    child_original = child.read_bytes()

    assert run_json(tmp_path, capsys, "task", "finish", "parent")[0] == 0
    parent_archive = tmp_path / "docs/archive/tasks/parent.md"
    parent_original = parent_archive.read_bytes()
    assert not parent.exists()
    assert child.read_bytes() == child_original

    assert run_json(tmp_path, capsys, "task", "cancel", "child", "--reason", "This branch is no longer needed.")[0] == 0
    assert not child.exists()
    assert parent_archive.read_bytes() == parent_original
    child_frontmatter, _ = parse_frontmatter((tmp_path / "docs/archive/tasks/child.md").read_text())
    assert child_frontmatter["status"] == "canceled"
    assert child_frontmatter["parent"] == "parent"


def test_cli_doctor_and_finish_share_archive_conflict_verdict(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    source = add_task(tmp_path, "task.md", task_text("one", status="doing"))
    archive = tmp_path / "docs/archive/tasks/task.md"
    archive.write_text("existing historical content", encoding="utf-8")
    before = snapshot(tmp_path)

    doctor_code, doctor = run_json(tmp_path, capsys, "task", "doctor", "one")
    finish_code, finish = run_json(tmp_path, capsys, "task", "finish", "one")

    assert doctor_code == finish_code == 2
    assert doctor["data"]["finish_ready"] is False
    assert doctor["problems"][0]["code"] == finish["problems"][0]["code"] == "archive_task_conflict"
    assert source.exists()
    assert snapshot(tmp_path) == before


def test_cli_doctor_is_read_only_and_finish_accepts_the_same_document(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "task.md", "---\nid: one\ntitle: Authored task\nstatus: todo\n---\n\nNo prescribed sections.\n")
    before = snapshot(tmp_path)

    code, doctor = run_json(tmp_path, capsys, "task", "doctor", "one")
    assert code == 0
    assert doctor["data"]["finish_ready"] is True
    assert doctor["data"]["changed"] is False
    assert snapshot(tmp_path) == before
    assert run_json(tmp_path, capsys, "task", "finish", "one")[0] == 0


@pytest.mark.parametrize("failure", ["stage", "publish", "remove_source"])
def test_archive_failure_keeps_original_task_and_removes_partial_archive(tmp_path: Path, monkeypatch, capsys, failure: str) -> None:
    write_workspace(tmp_path)
    source = add_task(tmp_path, "task.md", task_text("one", status="doing"))
    archive = tmp_path / "docs/archive/tasks/task.md"
    before = snapshot(tmp_path)

    if failure == "stage":
        def fail_stage(*args, **kwargs):
            raise OSError("simulated disk write failure")
        monkeypatch.setattr(cli, "write_temporary_text", fail_stage)
    elif failure == "publish":
        replace = os.replace
        def fail_publish(old, new):
            if Path(new) == archive:
                raise OSError("simulated archive publish failure")
            return replace(old, new)
        monkeypatch.setattr(cli.os, "replace", fail_publish)
    else:
        unlink = Path.unlink
        def fail_remove(path, *args, **kwargs):
            if path == source:
                raise OSError("simulated live document removal failure")
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", fail_remove)

    code, payload = run_json(tmp_path, capsys, "task", "finish", "one")
    assert code == 2
    assert payload["problems"][0]["code"] == "io_error"
    assert snapshot(tmp_path) == before


def test_prepared_finish_does_not_overwrite_concurrent_document_edit(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    source = add_task(tmp_path, "task.md", task_text("one", status="doing"))
    result = finish_task(tmp_path, "one")
    edited = source.read_text(encoding="utf-8") + "\nA human added this after preparation.\n"
    source.write_text(edited, encoding="utf-8")

    with pytest.raises(RepoctlError) as caught:
        cli.apply_task_result(tmp_path, result)
    assert caught.value.code == "task_changed"
    assert source.read_text(encoding="utf-8") == edited
    assert not (tmp_path / result["new_path"]).exists()


@pytest.mark.parametrize("create", [False, True])
def test_start_publish_failure_leaves_no_partial_task_or_auxiliary_state(tmp_path: Path, monkeypatch, capsys, create: bool) -> None:
    write_workspace(tmp_path)
    if not create:
        add_task(tmp_path, "task.md", task_text("one"))
    before = snapshot(tmp_path)
    replace = os.replace

    def fail_task_publish(old, new):
        if Path(new).parent == tmp_path / "docs/tasks":
            raise OSError("simulated Task publish failure")
        return replace(old, new)

    monkeypatch.setattr(cli.os, "replace", fail_task_publish)
    command = ["task", "create", "New Task", "--start"] if create else ["task", "start", "one"]
    code, payload = run_json(tmp_path, capsys, *command)
    assert code == 2
    assert payload["problems"][0]["code"] == "io_error"
    assert snapshot(tmp_path) == before
