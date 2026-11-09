"""Regression for a verified sibling's one disjoint committed path.

The source is a synthetic tmp_path Git repo; no user repository/receipt is modified.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.task.test_task_parent_lifecycle import _parent_child_repo_fixture
from tests.repoctl.task_lifecycle_helpers import commit_all, record_discovery


def test_path_scoped_committed_sibling_ack_preserves_original_receipt(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    parent, current, sibling = [f"T-2026060918414{i}Z" for i in range(6, 9)]
    repo = _parent_child_repo_fixture(tmp_path, parent, [current, sibling])
    release = repo / "docs/RELEASE.md"
    release.parent.mkdir()
    release.write_text("release base\n", encoding="utf-8")
    readme = repo / "README.md"
    readme.write_text("readme base\n", encoding="utf-8")
    commit_all(repo, "baseline for both")
    # An inherited dirty release file was present at task start, but never
    # committed as current task work. It is restored to its original Git tree.
    release.write_text("uncommitted historical overlay\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    def run(*args: str) -> tuple[int, dict]:
        code = main(["task", *args, "--json"])
        return code, json.loads(capsys.readouterr().out)

    assert run("start", current, "--force-dirty")[0] == 0
    # The legacy task recorded this inherited path as "task" ownership even
    # though it was restored before the separately completed sibling commit.
    assert run("baseline", "resolve", current, "--path", "docs/RELEASE.md", "--ownership", "task")[0] == 0
    record_discovery(tmp_path, current, chosen="repos/app.py,repos/README.md")
    (repo / "app.py").write_text("integrated source = 2\n", encoding="utf-8")
    readme.write_text("integrated README\n", encoding="utf-8")
    subprocess.run(["git", "restore", "--", "docs/RELEASE.md"], cwd=repo, check=True)
    commit_all(repo, "integrated source")
    assert run("start", sibling)[0] == 0
    record_discovery(tmp_path, sibling, chosen="repos/README.md,repos/docs/RELEASE.md")
    readme.write_text("integrated README plus sibling TTL\n", encoding="utf-8")
    release.write_text("release base\nTTL documented\n", encoding="utf-8")
    commit_all(repo, "completed sibling TTL")
    assert run("finish", sibling, "--use-committed-diff")[0] == 0
    receipt_path = tmp_path / f"docs/tasks/.repoctl-state/completions/{sibling}.json"
    original_receipt = receipt_path.read_bytes()

    full = ("--acknowledge-completed", sibling)
    only_release = ("--acknowledge-completed", f"{sibling}:docs/RELEASE.md")
    assert run("doctor", current, "--use-committed-diff")[1]["data"]["finish_ready"] is False
    for attempted in (
        full,
        ("--acknowledge-completed", f"{sibling}:README.md"),
        ("--acknowledge-completed", f"{sibling}:missing.md"),
        ("--acknowledge-completed", f"{sibling}:../docs/RELEASE.md"),
        ("--acknowledge-completed", f"{sibling}:docs/RELEASE.md",
         "--acknowledge-completed", f"{sibling}:README.md"),
    ):
        code, result = run("doctor", current, "--use-committed-diff", *attempted)
        assert code != 0
        assert result["data"]["finish_ready"] is False

    code, passed = run("doctor", current, "--use-committed-diff", *only_release)
    assert code == 0, passed
    assert passed["data"]["finish_ready"] is True
    assert run("finish", current, "--use-committed-diff", *only_release)[0] == 0
    receipt = json.loads(
        (tmp_path / f"docs/tasks/.repoctl-state/completions/{current}.json").read_text()
    )
    assert {entry["path"] for entry in receipt["changed_entries"]} == {"app.py", "README.md"}
    assert receipt_path.read_bytes() == original_receipt
    assert "selected paths docs/RELEASE.md" in (
        tmp_path / f"docs/tasks/{current}--child-1.md"
    ).read_text()
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=repo).decode() == ""


def test_path_scoped_ack_blocks_pending_worktree_edit(tmp_path: Path, monkeypatch, capsys) -> None:
    # This case uses the same two-task boundary with a later source-side edit.
    parent, current, sibling = [f"T-2026060918415{i}Z" for i in range(6, 9)]
    repo = _parent_child_repo_fixture(tmp_path, parent, [current, sibling])
    release = repo / "release.md"
    release.write_text("original\n")
    commit_all(repo, "baseline")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    def run(*args):
        code = main(["task", *args, "--json"])
        return code, json.loads(capsys.readouterr().out)

    assert run("start", current)[0] == 0
    record_discovery(tmp_path, current, chosen="repos/app.py")
    (repo / "app.py").write_text("integrated = 2\n")
    commit_all(repo, "current integrated")
    assert run("start", sibling)[0] == 0
    record_discovery(tmp_path, sibling, chosen="repos/release.md")
    release.write_text("sibling edit\n")
    commit_all(repo, "sibling edit")
    assert run("finish", sibling, "--use-committed-diff")[0] == 0
    release.write_text("uncommitted drift\n")
    code, reply = run(
        "doctor", current, "--use-committed-diff",
        "--acknowledge-completed", f"{sibling}:release.md",
    )
    assert code != 0
    assert any(
        problem["code"] == "completion_acknowledgement_invalid"
        for problem in reply["problems"]
    )
