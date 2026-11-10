"""Committed child observations are clipped to a root parent's recorded range.

These synthetic Git histories reproduce a late parent with nested child ranges;
no workspace authority or product history is rewritten during parent closure.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.repoctl.cli import main
from tests.repoctl.task.test_task_parent_lifecycle import (
    _parent_child_repo_fixture,
    _repo_snapshot,
)
from tests.repoctl.task_lifecycle_helpers import commit_all, record_discovery


def _runner(root: Path, monkeypatch, capsys):
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)

    def run(*args: str) -> tuple[int, dict]:
        code = main(["task", *args, "--json"])
        return code, json.loads(capsys.readouterr().out)

    return run


def _receipt(root: Path, task_id: str) -> Path:
    return root / f"docs/tasks/.repoctl-state/completions/{task_id}.json"


def _authority(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in (root / "docs").rglob("*")
        if p.is_file() and "debug" not in p.parts
    }


def _reject_parent(root: Path, run, parent: str) -> None:
    before = _authority(root)
    for command in ("doctor", "finish"):
        code, result = run(command, parent)
        assert code != 0 and result["ok"] is False, result
        if command == "doctor":
            assert result["data"]["finish_ready"] is False
    assert _authority(root) == before
    assert not _receipt(root, parent).exists()


def test_late_parent_composes_nested_committed_observations_without_rewriting_authority(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    parent, integration, first, second = [
        f"T-2026060918454{i}Z" for i in range(6, 10)
    ]
    repo = _parent_child_repo_fixture(tmp_path, parent, [integration, first, second])
    paths = ["shared.md", "settled.md", "long-only.md"]
    for path in paths:
        (repo / path).write_text("original\n", encoding="utf-8")
    commit_all(repo, "initial source")
    run = _runner(tmp_path, monkeypatch, capsys)
    assert run("start", integration)[0] == 0
    record_discovery(
        tmp_path, integration, chosen=",".join(f"repos/{path}" for path in paths),
    )
    for path in paths:
        (repo / path).write_text("already present when parent started\n", encoding="utf-8")
    commit_all(repo, "integration prefix before parent")
    assert run("start", parent)[0] == 0

    for child, value in [(first, "first nested change\n"), (second, "second nested change\n")]:
        assert run("start", child)[0] == 0
        record_discovery(tmp_path, child, chosen="repos/shared.md")
        (repo / "shared.md").write_text(value, encoding="utf-8")
        commit_all(repo, child)
        code, result = run("finish", child, "--use-committed-diff")
        assert code == 0, result
    (repo / "long-only.md").write_text("integration suffix after parent\n", encoding="utf-8")
    commit_all(repo, "integration suffix")
    code, result = run("finish", integration, "--use-committed-diff")
    assert code == 0, result

    state = tmp_path / "docs/tasks/.repoctl-state"
    protected = {
        p: p.read_bytes()
        for p in [*state.glob("T-*.json"), *(state / "completions").glob("*.json")]
    }
    child_bytes = {
        p.name: p.read_bytes()
        for p in (tmp_path / "docs/tasks").glob("T-*.md")
        if not p.name.startswith(parent)
    }
    product = _repo_snapshot(repo, paths)
    code, doctor = run("doctor", parent)
    assert code == 0 and doctor["data"]["finish_ready"] is True, doctor
    code, finished = run("finish", parent)
    assert code == 0 and finished["data"]["archived"] is True, finished
    assert all(p.read_bytes() == data for p, data in protected.items())
    assert all(
        (tmp_path / "docs/archive/tasks" / name).read_bytes() == data
        for name, data in child_bytes.items()
    )
    assert _repo_snapshot(repo, paths) == product
    parent_receipt = json.loads(_receipt(tmp_path, parent).read_text())
    assert parent_receipt["changed_entries"] == []
    assert parent_receipt["repo_evidence"]["mode"] == "none"


@pytest.mark.parametrize("gap", ["between-children", "after-terminal-restored"])
def test_committed_parent_rejects_unobserved_transition_on_claimed_path(
    tmp_path: Path, monkeypatch, capsys, gap: str,
) -> None:
    parent, first, second = [f"T-2026060918464{i}Z" for i in range(6, 9)]
    repo = _parent_child_repo_fixture(tmp_path, parent, [first, second])
    run = _runner(tmp_path, monkeypatch, capsys)
    assert run("start", parent)[0] == 0
    for child, value in [(first, "first = 2\n"), (second, "second = 3\n")]:
        assert run("start", child)[0] == 0
        record_discovery(tmp_path, child, chosen="repos/app.py")
        (repo / "app.py").write_text(value, encoding="utf-8")
        commit_all(repo, child)
        assert run("finish", child, "--use-committed-diff")[0] == 0
        if child == first and gap == "between-children":
            (repo / "app.py").write_text("unobserved = 99\n", encoding="utf-8")
            commit_all(repo, "unobserved change between completed children")
    if gap == "after-terminal-restored":
        # A net diff cannot prove that no unrelated transition occurred.
        code, ready = run("doctor", parent)
        assert code == 0 and ready["data"]["finish_ready"] is True, ready
        completed = (repo / "app.py").read_bytes()
        (repo / "app.py").write_text("unobserved later = 99\n", encoding="utf-8")
        commit_all(repo, "unobserved later change")
        (repo / "app.py").write_bytes(completed)
        commit_all(repo, "restore terminal bytes")
    _reject_parent(tmp_path, run, parent)


def _single_committed_child(root: Path, monkeypatch, capsys):
    parent, child = [f"T-2026060918474{i}Z" for i in range(6, 8)]
    repo = _parent_child_repo_fixture(root, parent, [child])
    (repo / "other.py").write_text("different = 99\n", encoding="utf-8")
    commit_all(repo, "different source for endpoint corruption")
    run = _runner(root, monkeypatch, capsys)
    assert run("start", parent)[0] == 0
    assert run("start", child)[0] == 0
    record_discovery(root, child, chosen="repos/app.py")
    (repo / "app.py").write_text("child = 2\n", encoding="utf-8")
    commit_all(repo, "completed child")
    assert run("finish", child, "--use-committed-diff")[0] == 0
    code, ready = run("doctor", parent)
    assert code == 0 and ready["data"]["finish_ready"] is True, ready
    return repo, parent, child, run


def test_committed_parent_rejects_child_from_forked_history(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    repo, parent, child, run = _single_committed_child(tmp_path, monkeypatch, capsys)
    receipt = json.loads(_receipt(tmp_path, child).read_text())
    evidence = receipt["repo_evidence"]
    completed = (repo / "app.py").read_bytes()
    subprocess.run(
        ["git", "checkout", "-b", "independent-parent-work", evidence["start_head"]],
        cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    (repo / "app.py").write_bytes(completed)
    commit_all(repo, "independent matching content")
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", evidence["observed_head"], "HEAD"],
        cwd=repo, check=False,
    ).returncode == 1
    _reject_parent(tmp_path, run, parent)


@pytest.mark.parametrize("endpoint", ["before", "after"])
def test_committed_parent_requires_receipt_endpoints_to_match_git(
    tmp_path: Path, monkeypatch, capsys, endpoint: str,
) -> None:
    repo, parent, child, run = _single_committed_child(tmp_path, monkeypatch, capsys)
    path = _receipt(tmp_path, child)
    receipt = json.loads(path.read_text())
    other_oid = subprocess.check_output(
        ["git", "rev-parse", "HEAD:other.py"], cwd=repo, text=True,
    ).strip()
    # The digest is well formed and names a real blob; it is not this path's
    # recorded Git endpoint. The whole immutable receipt must still be checked.
    receipt["repo_evidence"]["path_transitions"][0][endpoint]["blob_oid"] = other_oid
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    _reject_parent(tmp_path, run, parent)
