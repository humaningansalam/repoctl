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


def test_resume_returns_a_unique_task_or_selection_choices(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    code, empty = run_json(tmp_path, capsys, "task", "resume")
    assert code == 0
    assert empty["data"] == {"task": None, "candidates": []}

    add_task(tmp_path, "first.md", task_text("first", body="## Handoff\n\nReview retry handling in src/payments.py.\n"))
    code, single = run_json(tmp_path, capsys, "task", "resume")
    assert code == 0
    assert single["data"]["task"]["id"] == "first"
    assert single["data"]["handoff"] == "Review retry handling in src/payments.py."

    add_task(tmp_path, "second.md", task_text("second", status="doing"))
    code, choices = run_json(tmp_path, capsys, "task", "resume")
    assert code == 0
    assert choices["data"]["task"] is None
    assert {task["id"] for task in choices["data"]["candidates"]} == {"first", "second"}

    code, selected = run_json(tmp_path, capsys, "task", "resume", "second")
    assert code == 0
    assert selected["data"]["task"]["id"] == "second"
