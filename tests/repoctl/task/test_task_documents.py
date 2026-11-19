from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.repoctl.io import RepoctlError
from tools.repoctl.markdown import parse_frontmatter, replace_frontmatter_line
from tools.repoctl.tasks import (
    append_task_log, block_task, cancel_task, create_task_file, finish_task,
    load_tasks, normalize_task_id, resolve_task, start_task,
    task_discovery_values, task_handoff_body, update_task_discovery, validate_tasks,
)


def publish_fixture(root: Path, result: dict) -> Path:
    """Materialize a prepared document in this disposable test workspace."""
    target = root / result["new_path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(result["text"].encode("utf-8"))
    if result["old_path"] and result["old_path"] != result["new_path"]:
        (root / result["old_path"]).unlink()
    return target


def task_fixture(root: Path, title: str = "작업 조사", **fields) -> tuple[str, Path]:
    result = create_task_file(root, title=title, **fields)
    return result["task_id"], publish_fixture(root, result)


def snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_create_accepts_korean_title_and_does_not_write_or_require_board(tmp_path: Path) -> None:
    result = create_task_file(tmp_path, title="새로운 설계 검토", area="research", repo_id="main", repo_ref="feature", parent="completed-parent")
    assert snapshot(tmp_path) == {}
    assert "새로운-설계-검토" in result["new_path"]
    fields, body = parse_frontmatter(result["text"])
    assert fields["area"] == "research" and fields["repo_id"] == "main"
    assert fields["parent"] == "completed-parent"
    assert "generated-handoff" not in body
    publish_fixture(tmp_path, result)
    assert resolve_task(tmp_path, result["task_id"]).title == "새로운 설계 검토"


def test_create_ids_do_not_require_clock_wait(tmp_path: Path) -> None:
    first = create_task_file(tmp_path, title="A")
    second = create_task_file(tmp_path, title="A")
    assert first["task_id"] != second["task_id"]
    assert normalize_task_id(first["new_path"]) == first["task_id"]


def test_exact_id_lookup_does_not_depend_on_timestamp_filename_or_locator(tmp_path: Path) -> None:
    path = tmp_path / "docs/archive/tasks/previous-plan.md"
    path.parent.mkdir(parents=True)
    path.write_text('---\nid: "project/기획"\nstatus: done\ntitle: Prior plan\n---\n\n## Goal\n\nOriginal.\n')
    task = resolve_task(tmp_path, "project/기획")
    assert task.path == path and task.archived
    assert not (tmp_path / "docs/tasks/.repoctl-state").exists()


def test_start_and_finish_ignore_handoff_absence_git_state_and_prose_timestamps(tmp_path: Path) -> None:
    task_id, path = task_fixture(tmp_path, repo_id="main")
    fields, _ = parse_frontmatter(path.read_text())
    authored = "## Goal\n\nCreate a new file.\n\n## Execution Log\n\n~~~text\n- 29990101T000000Z: example only\n~~~\n"
    text = "---\n" + "".join(f"{key}: {json.dumps(value, ensure_ascii=False)}\n" for key, value in fields.items()) + "---\n\n" + authored
    path.write_text(text)
    product = tmp_path / "repos/pending.py"
    product.parent.mkdir()
    product.write_text("uncommitted = True\n")
    before = snapshot(tmp_path)
    prepared = start_task(tmp_path, task_id)
    assert snapshot(tmp_path) == before
    publish_fixture(tmp_path, prepared)
    done = finish_task(tmp_path, task_id)
    assert parse_frontmatter(done["text"])[1] == parse_frontmatter(text)[1]
    assert parse_frontmatter(done["text"])[0]["completed_at"]
    assert product.read_text() == "uncommitted = True\n"
    assert not (tmp_path / "docs/tasks/.repoctl-state").exists()


def test_handoff_is_readable_without_binding_or_existing_first_file(tmp_path: Path) -> None:
    task_id, path = task_fixture(tmp_path)
    text = path.read_text().replace("## Handoff\n\n", "## Handoff\n\nCreate the new design.\n- First file to open: \x60docs/future.md\x60\n\n")
    path.write_text(text)
    assert "docs/future.md" in task_handoff_body(resolve_task(tmp_path, task_id))
    assert parse_frontmatter(start_task(tmp_path, task_id)["text"])[0]["status"] == "doing"
    assert validate_tasks(load_tasks(tmp_path)) == []


def test_parent_and_children_have_independent_status_and_archive(tmp_path: Path) -> None:
    parent, parent_path = task_fixture(tmp_path, "Parent", repo_id="main")
    child, child_path = task_fixture(tmp_path, "Child", parent=parent, repo_id="main")
    original_child = child_path.read_bytes()
    parent_archive = publish_fixture(tmp_path, finish_task(tmp_path, parent))
    assert child_path.read_bytes() == original_child
    assert resolve_task(tmp_path, child).status == "todo"
    assert not parent_path.exists() and parent_archive.exists()
    assert validate_tasks(load_tasks(tmp_path, include_archived=True)) == []
    original_parent = parent_archive.read_bytes()
    child_archive = publish_fixture(tmp_path, cancel_task(tmp_path, child, reason="Superseded"))
    assert child_archive.exists() and not child_path.exists()
    assert parent_archive.read_bytes() == original_parent


def test_closed_child_is_not_rewritten_when_parent_finishes(tmp_path: Path) -> None:
    parent, _ = task_fixture(tmp_path, "Parent")
    child, _ = task_fixture(tmp_path, "Child", parent=parent)
    child_archive = publish_fixture(tmp_path, finish_task(tmp_path, child))
    before = child_archive.read_bytes()
    publish_fixture(tmp_path, finish_task(tmp_path, parent))
    assert child_archive.read_bytes() == before


def test_task_can_reference_an_already_completed_parent(tmp_path: Path) -> None:
    parent, _ = task_fixture(tmp_path, "Parent")
    publish_fixture(tmp_path, finish_task(tmp_path, parent))
    child, _ = task_fixture(tmp_path, "Follow-up grouping", parent=parent)
    assert resolve_task(tmp_path, child).parent == parent
    assert not validate_tasks(load_tasks(tmp_path, include_archived=True))


def test_cycle_is_diagnosed_without_creating_a_completion_gate(tmp_path: Path) -> None:
    first, first_path = task_fixture(tmp_path, "A")
    second, _ = task_fixture(tmp_path, "B", parent=first)
    first_path.write_text(replace_frontmatter_line(first_path.read_text(), "parent", json.dumps(second)))
    problems = validate_tasks(load_tasks(tmp_path))
    assert any(problem.code == "parent_cycle" for problem in problems)
    assert parse_frontmatter(finish_task(tmp_path, first)["text"])[0]["status"] == "done"


def test_duplicate_frontmatter_cannot_read_one_value_and_write_another() -> None:
    text = "---\nid: sample\nstatus: todo\nstatus: blocked\n---\nBody\n"
    with pytest.raises(RepoctlError) as error:
        parse_frontmatter(text)
    assert error.value.code == "duplicate_frontmatter_key"
    with pytest.raises(RepoctlError):
        replace_frontmatter_line(text, "status", "doing")


def test_duplicate_handoff_is_diagnosed_but_fenced_examples_are_not_sections(tmp_path: Path) -> None:
    task_id, path = task_fixture(tmp_path)
    path.write_text(path.read_text() + "\n~~~~markdown\n~~~\n## Handoff\nexample\n~~~~\n")
    assert not validate_tasks(load_tasks(tmp_path))
    path.write_text(path.read_text() + "\n## Handoff\n\nConflicting instruction.\n")
    assert any(problem.code == "duplicate_section" for problem in validate_tasks(load_tasks(tmp_path)))
    with pytest.raises(RepoctlError) as error:
        task_handoff_body(resolve_task(tmp_path, task_id))
    assert error.value.code == "duplicate_section"


def test_status_edits_preserve_authored_body_and_crlf(tmp_path: Path) -> None:
    task_id, path = task_fixture(tmp_path)
    original = path.read_text().replace("\n", "\r\n")
    path.write_bytes(original.encode())
    result = block_task(tmp_path, task_id)
    assert parse_frontmatter(result["text"])[1] == parse_frontmatter(original)[1]
    assert "\r\nstatus: blocked\r\n" in result["text"]
    assert result["original_text"].encode() == path.read_bytes()


def test_log_accepts_code_examples_and_many_file_references(tmp_path: Path) -> None:
    task_id, path = task_fixture(tmp_path)
    message = "Decision: inspect these files\n" + "\n".join(f"repos/source-{index}.py" for index in range(12)) + "\n\x60\x60\x60python\nprint('example')\n\x60\x60\x60"
    result = append_task_log(tmp_path, task_id, message)
    assert "print('example')" in result["text"] and "source-11.py" in result["text"]
    assert path.read_text() == result["original_text"]


def test_discovery_is_advisory_and_can_be_cleared_without_reason(tmp_path: Path) -> None:
    task_id, _ = task_fixture(tmp_path)
    publish_fixture(tmp_path, update_task_discovery(tmp_path, task_id, chosen=["repos/new/file.py", "repos/directory/"]))
    task = resolve_task(tmp_path, task_id)
    assert task_discovery_values(task)["Chosen files"] == ["repos/new/file.py", "repos/directory/"]
    publish_fixture(tmp_path, update_task_discovery(tmp_path, task_id, replace_chosen=[]))
    assert task_discovery_values(resolve_task(tmp_path, task_id))["Chosen files"] == []


def test_archive_conflict_does_not_overwrite_existing_history(tmp_path: Path) -> None:
    task_id, path = task_fixture(tmp_path)
    target = tmp_path / "docs/archive/tasks" / path.name
    target.parent.mkdir(parents=True)
    target.write_text("existing history\n")
    before = snapshot(tmp_path)
    with pytest.raises(RepoctlError) as error:
        finish_task(tmp_path, task_id)
    assert error.value.code == "archive_task_conflict"
    assert snapshot(tmp_path) == before


def test_parent_scope_and_future_files_do_not_require_runtime_sidecars(tmp_path: Path) -> None:
    task_id, _ = task_fixture(tmp_path, area="custom", parent="external-reference", depends_on=["planned-task"])
    result = finish_task(tmp_path, task_id)
    publish_fixture(tmp_path, result)
    assert all(path.suffix == ".md" for path in tmp_path.rglob("*") if path.is_file())


def test_chosen_update_preserves_authored_notes_and_other_discovery_prose(tmp_path: Path) -> None:
    task_id, path = task_fixture(tmp_path)
    authored = "\n## Discovery\n\n- Chosen files:\n- Notes:\n  - Keep the public API.\n    Rationale spans multiple lines.\n\nAuthored explanation outside fields.\n"
    path.write_text(path.read_text() + authored)
    result = update_task_discovery(tmp_path, task_id, chosen=["repos/new.py"])
    assert "  - Keep the public API.\n    Rationale spans multiple lines.\n\nAuthored explanation outside fields.\n" in result["text"]


def test_html_comment_inside_fence_does_not_hide_real_section() -> None:
    from tools.repoctl.markdown import find_section
    text = "~~~~html\n<!-- example only\n## Handoff\n~~~~\n\n## Handoff\n\nActual instruction.\n"
    section = find_section(text, "Handoff")
    assert text[section.body_start:section.end].strip() == "Actual instruction."


def test_selected_task_ignores_unrelated_malformed_document(tmp_path: Path) -> None:
    task_id, path = task_fixture(tmp_path)
    archive = tmp_path / "docs/archive/tasks"
    archive.mkdir(parents=True)
    (archive / "broken.md").write_text("---\nid: broken\nstatus: done\nstatus: todo\n---\n")
    assert resolve_task(tmp_path, task_id).path == path
    assert resolve_task(tmp_path, path.relative_to(tmp_path).as_posix()).path == path
    assert parse_frontmatter(finish_task(tmp_path, task_id)["text"])[0]["status"] == "done"


def test_unknown_target_status_is_not_silently_changed(tmp_path: Path) -> None:
    task_id, path = task_fixture(tmp_path)
    path.write_text(replace_frontmatter_line(path.read_text(), "status", "typo"))
    before = path.read_bytes()
    with pytest.raises(RepoctlError) as error:
        start_task(tmp_path, task_id)
    assert error.value.code == "invalid_task_status"
    assert path.read_bytes() == before


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_chosen_update_preserves_fenced_and_commented_examples(tmp_path: Path, newline: str) -> None:
    task_id, path = task_fixture(tmp_path)
    fenced = (
        "\x60\x60\x60\x60markdown\n- Chosen files:\n  - \x60example.py\x60\n"
        "<!-- an example with no closing comment marker\n\x60\x60\x60\x60\n"
    )
    commented = "Explanation <!--\n- Chosen files:\n  - \x60hidden.py\x60\n-->\n"
    note = "<!-- Keep the selection rationale. -->\n"
    authored = (
        "\n## Discovery\n\n" + fenced + "\n" + commented + "\n"
        "- Chosen files:\n" + note + "  - \x60actual.py\x60\n\n"
        "- Notes:\n  - Keep the public API.\n"
    )
    path.write_bytes((path.read_text() + authored).replace("\n", newline).encode())
    assert task_discovery_values(resolve_task(tmp_path, task_id))["Chosen files"] == ["actual.py"]
    result = update_task_discovery(tmp_path, task_id, chosen=["next.py"])
    for preserved in (fenced, commented, note, "- Notes:\n  - Keep the public API.\n"):
        assert preserved.replace("\n", newline) in result["text"]
    publish_fixture(tmp_path, result)
    assert task_discovery_values(resolve_task(tmp_path, task_id)) == {
        "Chosen files": ["actual.py", "next.py"],
        "Notes": ["Keep the public API."],
    }


def test_id_lookup_detects_duplicate_in_human_named_archive_before_reading_its_body(tmp_path: Path) -> None:
    task_id, path = task_fixture(tmp_path)
    duplicate = tmp_path / "docs/archive/tasks/prior-plan.md"
    duplicate.parent.mkdir(parents=True)
    header = f"---\nid: {task_id}\ntitle: Prior plan\nstatus: done\n---\n"
    duplicate.write_bytes(header.encode() + b"\xff unreadable body")
    with pytest.raises(RepoctlError) as error:
        resolve_task(tmp_path, task_id)
    assert error.value.code == "duplicate_task_id"
    selected = path.relative_to(tmp_path).as_posix()
    assert resolve_task(tmp_path, selected).path == path
    assert start_task(tmp_path, selected)["task_id"] == task_id


def test_id_lookup_consumes_only_unrelated_frontmatter(tmp_path: Path, monkeypatch) -> None:
    from contextlib import contextmanager

    task_id, path = task_fixture(tmp_path)
    unrelated = path.parent / "other-plan.md"
    header = b"---\nid: other\ntitle: Other plan\nstatus: todo\n---\n"
    unrelated.write_bytes(header + b"Unrelated body.\n" * 10000)
    opened_positions = []
    original_open = Path.open

    @contextmanager
    def observe_open(file, *args, **kwargs):
        with original_open(file, *args, **kwargs) as stream:
            yield stream
            if file == unrelated:
                opened_positions.append(stream.tell())

    monkeypatch.setattr(Path, "open", observe_open)
    selected = resolve_task(tmp_path, task_id)
    assert selected.path == path
    assert "## Goal" in selected.body
    assert opened_positions == [len(header)]


def test_doctor_reports_missing_selected_identity_as_json(tmp_path: Path, capsys) -> None:
    from tools.repoctl.cli import main

    path = tmp_path / "docs/tasks/no-id.md"
    path.parent.mkdir(parents=True)
    original = b"---\ntitle: Missing identity\nstatus: doing\n---\nKeep this body.\n"
    path.write_bytes(original)
    assert main(["--workspace-root", str(tmp_path), "task", "doctor",
                 "docs/tasks/no-id.md", "--json"]) != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["data"]["finish_ready"] is False
    assert payload["problems"][0]["code"] == "missing_task_id"
    assert payload["problems"][0]["path"] == "docs/tasks/no-id.md"
    assert path.read_bytes() == original
    assert not (tmp_path / "docs/archive/tasks/no-id.md").exists()


def test_selected_task_reports_its_own_malformed_frontmatter_by_id(tmp_path: Path) -> None:
    task_id, path = task_fixture(tmp_path)
    path.write_text(path.read_text().replace("status: \"todo\"\n", "status: todo\nstatus: blocked\n"))
    with pytest.raises(RepoctlError) as error:
        resolve_task(tmp_path, task_id)
    assert error.value.code == "duplicate_frontmatter_key"


@pytest.mark.parametrize("operation", ["finish", "log", "discovery"])
def test_edit_preparation_keeps_selected_source_and_publish_rejects_concurrent_change(
    tmp_path: Path, monkeypatch, operation: str,
) -> None:
    from tools.repoctl import tasks
    from tools.repoctl.cli import apply_task_result

    task_id, path = task_fixture(tmp_path)
    before = path.read_bytes()
    concurrent = before + b"\nConcurrent author change.\n"
    original_resolve = tasks.resolve_task

    def select_then_edit(root, reference):
        selected = original_resolve(root, reference)
        path.write_bytes(concurrent)
        return selected

    monkeypatch.setattr(tasks, "resolve_task", select_then_edit)
    if operation == "finish":
        result = finish_task(tmp_path, task_id)
    elif operation == "log":
        result = append_task_log(tmp_path, task_id, "Work note")
    else:
        result = update_task_discovery(tmp_path, task_id, chosen=["next.py"])
    assert result["original_text"].encode() == before
    assert "Concurrent author change." not in result["text"]
    with pytest.raises(RepoctlError) as error:
        apply_task_result(tmp_path, result)
    assert error.value.code == "task_changed"
    assert path.read_bytes() == concurrent
    assert not (tmp_path / "docs/archive/tasks" / path.name).exists()
