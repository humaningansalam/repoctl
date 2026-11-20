from __future__ import annotations

import json
import subprocess
from pathlib import Path

def write_workspace(root: Path) -> None:
    (root / "docs/tasks").mkdir(parents=True, exist_ok=True)
    (root / "docs/archive/tasks").mkdir(parents=True, exist_ok=True)


def task_text(
    task_id: str, *, status: str = "todo", parent: str = "", body: str = ""
) -> str:
    fields = {"id": task_id, "title": f"Task {task_id}", "status": status}
    if parent:
        fields["parent"] = parent
    return (
        "---\n"
        + "".join(f"{key}: {json.dumps(value)}\n" for key, value in fields.items())
        + "---\n"
        + body
    )


def add_task(root: Path, name: str, text: str) -> Path:
    path = root / "docs/tasks" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
