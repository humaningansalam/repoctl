from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.repoctl.cli import COMPACT_PATH_LIMIT, main
from tools.repoctl.tasks import live_chosen_overlaps, load_task, resolve_task
from tests.repoctl.repository.test_repository_task_scope import _setup_configured_multi_repo
from tests.repoctl.task_lifecycle_helpers import (
    add_task,
    init_committed_product_repo,
    record_discovery,
    task_text,
    write_workspace,
)

CURRENT = "T-20260609184201Z"
OTHER = "T-20260609184202Z"


def _add_task(
    root: Path,
    task_id: str,
    *,
    repo_id: str = "main",
    status: str = "todo",
    chosen: str = "",
) -> Path:
    text = (
        task_text(task_id, status=status)
        .replace('area: ""', 'area: "repo"' if repo_id else 'area: "ops"')
        .replace('repo_id: ""', f'repo_id: "{repo_id}"')
    )
    path = add_task(root, f"{task_id}--overlap.md", text)
    if chosen:
        record_discovery(root, task_id, chosen=chosen)
    board_paths = sorted(
        f"docs/tasks/{item.name}"
        for item in (root / "docs/tasks").glob("T-*--*.md")
    )
    (root / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n"
        + "".join(f"- {item}\n" for item in board_paths)
        + "\n## Backlog\n",
        encoding="utf-8",
    )
    return path


def _setup(root: Path, monkeypatch) -> Path:
    write_workspace(root)
    repo = root / "repos"
    init_committed_product_repo(
        repo,
        {"src/shared.py": "shared = 1\n", "src/other.py": "other = 1\n"},
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)
    return repo


def _run(capsys, *args: str) -> tuple[int, dict]:
    code = main(["task", *args, "--json"])
    return code, json.loads(capsys.readouterr().out)


def _overlap_warnings(payload: dict) -> list[dict]:
    return [warning for warning in payload["warnings"] if warning["code"] == "task_chosen_overlap"]


def _protected_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / "docs").rglob("*")
        if path.is_file()
    }


def _git_observation(repo: Path) -> tuple[bytes, bytes, bytes]:
    return (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo),
        subprocess.check_output(["git", "status", "--porcelain=v2", "--untracked-files=all"], cwd=repo),
        (repo / ".git/index").read_bytes(),
    )


def test_overlap_warns_on_resulting_scope_resume_and_doctor_without_blocking(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    repo = _setup(tmp_path, monkeypatch)
    _add_task(tmp_path, OTHER, chosen="repos/src/shared.py")
    _add_task(tmp_path, CURRENT, chosen="repos/src/other.py")
    for task_id in (OTHER, CURRENT):
        assert _run(capsys, "start", task_id)[0] == 0

    code, added = _run(capsys, "discovery", "add", CURRENT, "--chosen", "repos/src/shared.py")
    assert code == 0 and added["ok"] is True
    expected = [{
        "current_task_id": CURRENT,
        "other_task_id": OTHER,
        "other_task_status": "doing",
        "repo_id": "main",
        "overlapping_paths": ["src/shared.py"],
        "overlapping_path_count": 1,
        "overlapping_paths_truncated": False,
    }]
    assert added["data"]["chosen_overlaps"] == expected
    assert _overlap_warnings(added)[0]["path"] == f"docs/tasks/{CURRENT}--overlap.md"
    # Bind the current scope while a peer also chooses it. The advisory alone
    # must not invalidate executable Handoff freshness.
    assert _run(capsys, "handoff", "bind", CURRENT)[0] == 0
    before_git = _git_observation(repo)
    before_docs = _protected_bytes(tmp_path)
    code, resumed = _run(capsys, "resume", CURRENT)
    assert code == 0
    assert resumed["data"]["chosen_overlaps"] == expected
    assert _overlap_warnings(resumed)
    assert resumed["data"]["resume_guidance"]["status"] == "current"
    assert resumed["data"]["resume_guidance"]["blocked_by_health"] is False
    code, doctor = _run(capsys, "doctor", CURRENT)
    assert code == 0 and doctor["data"]["finish_ready"] is True
    assert doctor["data"]["chosen_overlaps"] == expected
    assert _overlap_warnings(doctor)
    assert _protected_bytes(tmp_path) == before_docs
    assert _git_observation(repo) == before_git

    code, ambiguous = _run(capsys, "resume")
    assert code != 0
    assert ambiguous["data"]["selection"]["status"] == "ambiguous"
    assert ambiguous["data"]["task"] is None
    assert {task["id"] for task in ambiguous["data"]["candidates"]} == {CURRENT, OTHER}
    assert not _overlap_warnings(ambiguous)

    # A note-only update uses the existing scope; replacement uses its result.
    code, unchanged = _run(capsys, "discovery", "add", CURRENT, "--note", "Coordinate shared source")
    assert code == 0 and unchanged["data"]["chosen_overlaps"] == expected
    code, replaced = _run(
        capsys, "discovery", "add", CURRENT,
        "--replace-chosen", "repos/src/other.py", "--reason", "Scope moved",
    )
    assert code == 0
    assert replaced["data"]["chosen_overlaps"] == []
    assert not _overlap_warnings(replaced)
    assert not _overlap_warnings(_run(capsys, "resume", CURRENT)[1])
    assert not _overlap_warnings(_run(capsys, "doctor", CURRENT)[1])

    assert _run(capsys, "discovery", "add", CURRENT, "--chosen", "repos/src/shared.py")[0] == 0
    code, finished = _run(capsys, "finish", CURRENT)
    assert code == 0 and finished["data"]["status"] == "done"
    code, single = _run(capsys, "resume")
    assert code == 0
    assert single["data"]["selection"]["status"] == "single_live"
    assert single["data"]["task"]["id"] == OTHER
    assert single["data"]["chosen_overlaps"] == []
    assert not _overlap_warnings(single)


@pytest.mark.parametrize("current_status,other_status,expected", [
    ("todo", "doing", True),
    ("doing", "todo", True),
    ("blocked", "blocked", True),
    ("doing", "done", False),
    ("doing", "canceled", False),
    ("done", "doing", False),
    ("canceled", "doing", False),
])
def test_overlap_respects_both_task_lifecycles(
    tmp_path: Path, monkeypatch, current_status: str, other_status: str, expected: bool,
) -> None:
    _setup(tmp_path, monkeypatch)
    _add_task(tmp_path, CURRENT, status=current_status, chosen="repos/src/shared.py")
    _add_task(tmp_path, OTHER, status=other_status, chosen="repos/src/shared.py")
    before = _protected_bytes(tmp_path)
    overlaps = live_chosen_overlaps(tmp_path, resolve_task(tmp_path, CURRENT))
    assert bool(overlaps) is expected
    if expected:
        assert overlaps[0]["other_task_status"] == other_status
    assert _protected_bytes(tmp_path) == before


def test_overlap_excludes_archived_task_even_with_live_frontmatter(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    _add_task(tmp_path, CURRENT, status="doing", chosen="repos/src/shared.py")
    other = _add_task(tmp_path, OTHER, status="doing", chosen="repos/src/shared.py")
    archive = tmp_path / "docs/archive/tasks" / other.name
    other.rename(archive)
    assert live_chosen_overlaps(tmp_path, resolve_task(tmp_path, CURRENT)) == []
    assert live_chosen_overlaps(tmp_path, load_task(archive, tmp_path, archived=True)) == []


def test_same_relative_path_in_different_repositories_has_no_overlap(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    _setup_configured_multi_repo(tmp_path, monkeypatch)
    _add_task(tmp_path, OTHER, repo_id="web", chosen="repos/web/app.py")
    _add_task(tmp_path, CURRENT, repo_id="api")
    code, result = _run(capsys, "discovery", "add", CURRENT, "--chosen", "repos/api/app.py")
    assert code == 0
    assert result["data"]["chosen_overlaps"] == []
    assert not _overlap_warnings(result)
    assert live_chosen_overlaps(tmp_path, resolve_task(tmp_path, OTHER)) == []


@pytest.mark.parametrize("root_task", [CURRENT, OTHER])
def test_overlap_does_not_infer_product_ownership_for_root_only_task(
    tmp_path: Path, monkeypatch, root_task: str,
) -> None:
    _setup(tmp_path, monkeypatch)
    for task_id in (CURRENT, OTHER):
        _add_task(
            tmp_path, task_id,
            repo_id="" if task_id == root_task else "main",
            chosen="repos/src/shared.py",
        )
    assert live_chosen_overlaps(tmp_path, resolve_task(tmp_path, CURRENT)) == []


@pytest.mark.parametrize("invalid_path", ["/src/shared.py", "repos/../src/shared.py", "repos//src/shared.py"])
def test_invalid_discovery_is_rejected_before_overlap_or_write(
    tmp_path: Path, monkeypatch, capsys, invalid_path: str,
) -> None:
    _setup(tmp_path, monkeypatch)
    for task_id in (CURRENT, OTHER):
        _add_task(tmp_path, task_id, chosen="repos/src/shared.py")
    before = _protected_bytes(tmp_path)
    code, result = _run(capsys, "discovery", "add", CURRENT, "--chosen", invalid_path)
    assert code != 0
    assert result["problems"][0]["code"] == "invalid_discovery_path"
    assert not _overlap_warnings(result)
    assert _protected_bytes(tmp_path) == before
    # Manually authored invalid values must not be normalized into conflicts.
    record_discovery(tmp_path, CURRENT, chosen=invalid_path)
    assert live_chosen_overlaps(tmp_path, resolve_task(tmp_path, CURRENT)) == []
    record_discovery(tmp_path, CURRENT, chosen="repos/src/shared.py")
    record_discovery(tmp_path, OTHER, chosen=invalid_path)
    assert live_chosen_overlaps(tmp_path, resolve_task(tmp_path, CURRENT)) == []


def test_multiple_manual_overlaps_are_sorted_bounded_and_read_only(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    repo = _setup(tmp_path, monkeypatch)
    others = ["T-20260609184204Z", "T-20260609184203Z"]
    paths = [f"src/file-{number:03}.py" for number in range(COMPACT_PATH_LIMIT + 7)]
    for task_id in (*others, CURRENT):
        _add_task(tmp_path, task_id)
        assert _run(capsys, "start", task_id)[0] == 0
        record_discovery(tmp_path, task_id, chosen=",".join(f"repos/{path}" for path in reversed(paths)))
    before_git = _git_observation(repo)
    before_docs = _protected_bytes(tmp_path)
    code, result = _run(capsys, "resume", CURRENT, "--full")
    assert code == 0
    overlaps = result["data"]["chosen_overlaps"]
    assert [item["other_task_id"] for item in overlaps] == sorted(others)
    for overlap in overlaps:
        assert overlap["overlapping_paths"] == paths[:COMPACT_PATH_LIMIT]
        assert overlap["overlapping_path_count"] == len(paths)
        assert overlap["overlapping_paths_truncated"] is True
    assert len(_overlap_warnings(result)) == 2
    assert paths[-1] not in " ".join(warning["message"] for warning in _overlap_warnings(result))
    assert _run(capsys, "resume", CURRENT, "--full")[1]["data"]["chosen_overlaps"] == overlaps
    code, doctor = _run(capsys, "doctor", CURRENT)
    assert code == 0 and doctor["data"]["finish_ready"] is True
    assert doctor["data"]["chosen_overlaps"] == overlaps
    assert len(_overlap_warnings(doctor)) == len(others)
    assert _protected_bytes(tmp_path) == before_docs
    assert _git_observation(repo) == before_git


def test_overlap_warning_keeps_actual_outside_scope_error(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    repo = _setup(tmp_path, monkeypatch)
    for task_id in (OTHER, CURRENT):
        _add_task(tmp_path, task_id, chosen="repos/src/shared.py")
        assert _run(capsys, "start", task_id)[0] == 0
    (repo / "src/outside.py").write_text("outside = 1\n", encoding="utf-8")
    code, result = _run(capsys, "doctor", CURRENT)
    assert code != 0
    assert result["data"]["finish_ready"] is False
    assert any(problem["code"] == "actual_changes_outside_chosen" for problem in result["problems"])
    assert _overlap_warnings(result)
