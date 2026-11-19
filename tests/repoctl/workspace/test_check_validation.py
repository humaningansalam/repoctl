from __future__ import annotations

from pathlib import Path

import pytest

from tests.repoctl.workspace.test_check import add_task, run_json, snapshot, task_text, write_workspace


def test_check_accepts_minimal_task_with_authored_prose(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    text = (
        "---\nid: freely-named\ntitle: Human work\nstatus: todo\n"
        "area: Customer interviews\nrepo_id: optional-descriptive-id\n---\n\n"
        "This Task needs no Board, index, Context Docs or repository.\n\n"
        "## Handoff\n\n- First file to open: future-file.py\n\n"
        "## Execution Log\n\nExamples may mention 2099-01-01 or /an/example/path.\n"
    )
    add_task(tmp_path, "human-work.md", text)
    before = snapshot(tmp_path)
    code, payload = run_json(tmp_path, capsys, "check")
    assert code == 0
    assert payload["data"]["task_count"] == 1
    assert payload["problems"] == []
    assert payload["warnings"] == []
    assert snapshot(tmp_path) == before


def test_check_reports_duplicate_identity_and_show_refuses_ambiguous_id(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "first.md", task_text("same"))
    add_task(tmp_path, "second.md", task_text("same"))
    before = snapshot(tmp_path)

    code, checked = run_json(tmp_path, capsys, "check")
    assert code == 1
    assert [problem["code"] for problem in checked["problems"]] == ["duplicate_task_id"]
    code, shown = run_json(tmp_path, capsys, "task", "show", "same")
    assert code == 2
    assert shown["problems"][0]["code"] == "duplicate_task_id"
    assert snapshot(tmp_path) == before


def test_parent_cycle_is_diagnostic_without_completion_gate(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    first = add_task(tmp_path, "first.md", task_text("first", parent="second"))
    second = add_task(tmp_path, "second.md", task_text("second", parent="first"))
    second_original = second.read_bytes()

    code, checked = run_json(tmp_path, capsys, "check")
    assert code == 0
    assert {warning["code"] for warning in checked["warnings"]} == {"parent_cycle"}
    assert run_json(tmp_path, capsys, "task", "finish", "first")[0] == 0
    assert not first.exists()
    assert second.read_bytes() == second_original


def test_archived_parent_remains_a_valid_classification_reference(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "parent.md", task_text("parent"))
    add_task(tmp_path, "child.md", task_text("child", parent="parent"))
    assert run_json(tmp_path, capsys, "task", "finish", "parent")[0] == 0

    code, checked = run_json(tmp_path, capsys, "check")
    assert code == 0
    assert not any(problem["code"] == "missing_parent" for problem in checked["warnings"])
    assert run_json(tmp_path, capsys, "task", "start", "child")[0] == 0


def test_unknown_status_is_readable_but_not_silently_rewritten(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "task.md", task_text("one", status="waiting-for-a-human"))
    before = snapshot(tmp_path)
    code, checked = run_json(tmp_path, capsys, "check")
    assert code == 0
    assert checked["warnings"][0]["code"] == "unknown_status"
    code, shown = run_json(tmp_path, capsys, "task", "show", "one")
    assert code == 0
    assert shown["data"]["status"] == "waiting-for-a-human"
    code, started = run_json(tmp_path, capsys, "task", "start", "one")
    assert code == 2
    assert started["problems"][0]["code"] == "invalid_task_status"
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("body,expected", [
    ("---\nid: one\nid: another\nstatus: todo\n---\n", "duplicate_frontmatter_key"),
    (task_text("one") + "\n## Handoff\n\nA second conflicting handoff.\n", "duplicate_section"),
])
def test_check_reports_ambiguous_document_structure(tmp_path: Path, capsys, body: str, expected: str) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "task.md", body)
    before = snapshot(tmp_path)
    code, payload = run_json(tmp_path, capsys, "check")
    assert code in {1, 2}
    assert payload["problems"][0]["code"] == expected
    assert snapshot(tmp_path) == before


def test_explicit_task_operations_ignore_unrelated_malformed_archive(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "task.md", task_text("one"))
    malformed = tmp_path / "docs/archive/tasks/broken.md"
    malformed.write_text("---\nid: bad\nid: ambiguous\n---\n", encoding="utf-8")
    original = malformed.read_bytes()

    assert run_json(tmp_path, capsys, "task", "start", "one")[0] == 0
    assert run_json(tmp_path, capsys, "task", "finish", "one")[0] == 0
    assert malformed.read_bytes() == original
    code, checked = run_json(tmp_path, capsys, "check", "--include-archived")
    assert code == 2
    assert checked["problems"][0]["code"] == "duplicate_frontmatter_key"
