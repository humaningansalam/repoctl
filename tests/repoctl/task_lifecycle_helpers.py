from __future__ import annotations

import subprocess
from pathlib import Path

def write_workspace(root: Path) -> None:
    (root / "docs/tasks").mkdir(parents=True, exist_ok=True)
    (root / "docs/archive/tasks").mkdir(parents=True, exist_ok=True)


def task_text(task_id: str, *, status: str = "todo", parent: str = "") -> str:
    return (
        "---\n"
        f"id: {task_id}\ntitle: \"Task {task_id}\"\nstatus: {status}\n"
        'owner: "unassigned"\nrepo_id: ""\nrepo_ref: ""\narea: ""\n'
        f'parent: "{parent}"\ndepends_on: []\ncreated: 20260609T184046Z\n'
        "---\n\n"
        f"# {task_id}\n\n## Goal\n\nInspect the actual source.\n\n"
        "## Handoff\n\n- Next exact step: inspect the source.\n"
        "- First file to open: \x60docs/BOARD.md\x60\n"
        "- First command to run: \x60repoctl check\x60\n"
        "- Done when: the behavior is understood.\n\n"
        "## Discovery\n\n- Chosen files:\n- Notes:\n\n"
        "## Execution Log\n\n## Verification\n"
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


def add_board_task(root: Path, name: str, text: str) -> Path:
    return add_task(root, name, text)
