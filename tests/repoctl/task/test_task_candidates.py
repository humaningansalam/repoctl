from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.task_lifecycle_helpers import (
    add_board_task, init_repo, task_text, write_workspace,
)


def test_frontend_backend_candidates_expose_title_area_without_extra_open(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    cases = [
        ("T-20260609184046Z", "Fix login message", "frontend", "doing"),
        ("T-20260609184047Z", "Protect login endpoint", "backend", "todo"),
    ]
    for task_id, title, area, status in cases:
        text = (
            task_text(task_id, status=status)
            .replace(f'title: "Task {task_id}"', f'title: "{title}"')
            .replace('area: ""', f'area: "{area}"')
            .replace('repo_id: ""', 'repo_id: "main"')
        )
        add_board_task(tmp_path, f"{task_id}--owner.md", text)
    (tmp_path / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n"
        + "".join(f"- docs/tasks/{task_id}--owner.md\n" for task_id, *_ in cases)
        + "\n## Backlog\n",
        encoding="utf-8",
    )

    assert main(["task", "resume", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["selection"]["status"] == "ambiguous"
    candidates = payload["data"]["candidates"]
    assert [(item["title"], item["area"], item["repo_id"], item["status"]) for item in candidates] == [
        ("Fix login message", "frontend", "main", "doing"),
        ("Protect login endpoint", "backend", "main", "todo"),
    ]
    assert all(set(item) == {
        "id", "title", "title_truncated", "area", "repo_id", "status", "path",
    } for item in candidates)

    assert main(["task", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["data"]["tasks"]
    assert [(item["title"], item["area"]) for item in listed] == [
        ("Fix login message", "frontend"), ("Protect login endpoint", "backend"),
    ]
