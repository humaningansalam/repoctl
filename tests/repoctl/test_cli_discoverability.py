from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.workspace.test_check import add_task, run_json, task_text, write_workspace


def test_version_does_not_need_a_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REPOCTL_WORKSPACE_ROOT", raising=False)
    assert main(["version", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "version"
    assert payload["data"]["version"]
    assert payload["ok"] is True
    assert list(tmp_path.iterdir()) == []


def test_json_options_work_before_or_after_leaf_command(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "task.md", task_text("one"))
    code, before = run_json(tmp_path, capsys, "task", "show", "one", "--summary")
    assert code == 0

    assert main(["task", "show", "one", "--summary", "--json", "--workspace-root", str(tmp_path)]) == 0
    after = json.loads(capsys.readouterr().out)
    assert after == before


def test_domain_and_argument_errors_keep_the_selected_command_identity(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    code, missing = run_json(tmp_path, capsys, "task", "show", "missing")
    assert code == 2
    assert missing["command"] == "task.show"
    assert missing["ok"] is False
    assert missing["problems"][0]["code"] == "task_not_found"

    code, argument = run_json(tmp_path, capsys, "task", "show")
    assert code == 2
    assert argument["command"] == "task.show"
    assert argument["problems"][0]["code"] == "argparse_error"


def test_resume_selects_a_unique_task_and_reports_choices_without_hidden_authority(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    code, empty = run_json(tmp_path, capsys, "task", "resume")
    assert code == 0
    assert empty["data"] == {"task": None, "candidates": []}

    add_task(tmp_path, "first.md", task_text("first"))
    code, single = run_json(tmp_path, capsys, "task", "resume")
    assert code == 0
    assert single["data"]["task"]["id"] == "first"
    assert "inspect the source" in single["data"]["handoff"]

    add_task(tmp_path, "second.md", task_text("second", status="doing"))
    code, choices = run_json(tmp_path, capsys, "task", "resume")
    assert code == 0
    assert choices["data"]["task"] is None
    assert {task["id"] for task in choices["data"]["candidates"]} == {"first", "second"}

    code, selected = run_json(tmp_path, capsys, "task", "resume", "second")
    assert code == 0
    assert selected["data"]["task"]["id"] == "second"


def test_discovery_updates_preserve_unmentioned_authored_notes(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    path = add_task(tmp_path, "task.md", task_text("one"))
    path.write_text(path.read_text() + "\n## Local considerations\n\nKeep this paragraph as written.\n", encoding="utf-8")

    assert run_json(tmp_path, capsys, "task", "discovery", "add", "one", "--chosen", "src/future.py", "--note", "Initial scope")[0] == 0
    assert run_json(tmp_path, capsys, "task", "discovery", "add", "one", "--replace-chosen", "docs/plan.md")[0] == 0
    code, discovery = run_json(tmp_path, capsys, "task", "show", "one", "--section", "Discovery")
    assert code == 0
    assert "docs/plan.md" in discovery["data"]["body"]
    assert "src/future.py" not in discovery["data"]["body"]
    assert "Initial scope" in discovery["data"]["body"]
    assert "Keep this paragraph as written." in path.read_text(encoding="utf-8")
