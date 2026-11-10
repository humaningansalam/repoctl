"""Regression for a verified sibling's one disjoint committed path.

The source is a synthetic tmp_path Git repo; no user repository/receipt is modified.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tools.repoctl.cli import main
from tests.repoctl.task.test_task_parent_lifecycle import _parent_child_repo_fixture, _repo_child_text
from tests.repoctl.task_lifecycle_helpers import (
    add_task, commit_all, init_committed_product_repo, record_discovery, task_text, write_workspace,
)
from tests.repoctl.repository.test_repositories import write_settings


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
    # Validate the whole receipt even when its corrupted transition was not selected.
    damaged_receipt = json.loads(original_receipt)
    unselected = next(
        item for item in damaged_receipt["repo_evidence"]["path_transitions"]
        if item["path"] == "README.md"
    )
    unselected["after"] = {"kind": "missing"}
    receipt_path.write_text(json.dumps(damaged_receipt) + "\n", encoding="utf-8")
    _assert_rejected(
        tmp_path, run, current, f"{sibling}:docs/RELEASE.md",
        codes={"child_completion_receipt_invalid"},
    )
    receipt_path.write_bytes(original_receipt)

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
    expected_ack = [{
        "task_id": sibling,
        "receipt_path": receipt_path.relative_to(tmp_path).as_posix(),
        "receipt_sha256": "sha256:" + hashlib.sha256(original_receipt).hexdigest(),
        "selected_paths": ["docs/RELEASE.md"],
    }]
    assert passed["data"]["acknowledged_completions"] == expected_ack
    code, finished = run("finish", current, "--use-committed-diff", *only_release)
    assert code == 0, finished
    assert finished["data"]["acknowledged_completions"] == expected_ack
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



def _committed_sibling_fixture(
    root: Path, monkeypatch, capsys, *, rename: bool = False,
    prior_change: bool = False, current_after_sibling: bool = False,
    sibling_status: str = "done",
):
    """Build real Git and lifecycle evidence, with disjoint current/sibling work."""
    parent, current, sibling = [f"T-2026060918424{i}Z" for i in range(6, 9)]
    repo = _parent_child_repo_fixture(root, parent, [current, sibling])
    source = "old.md" if rename else "shared.md"
    (repo / source).write_text("original sibling source\n", encoding="utf-8")
    (repo / "untouched.md").write_text("existing but not in the receipt\n", encoding="utf-8")
    commit_all(repo, "shared baseline")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)

    def run(*args: str) -> tuple[int, dict]:
        code = main(["task", *args, "--json"])
        return code, json.loads(capsys.readouterr().out)

    def start_current() -> None:
        assert run("start", current)[0] == 0
        record_discovery(root, current, chosen="repos/app.py")
        (repo / "app.py").write_text("current = 2\n", encoding="utf-8")
        if prior_change:
            (repo / source).write_text("earlier current transition\n", encoding="utf-8")
        commit_all(repo, "current work")

    if not current_after_sibling:
        start_current()
    if sibling_status != "todo":
        assert run("start", sibling)[0] == 0
        record_discovery(
            root, sibling,
            chosen="repos/old.md,repos/new.md" if rename else "repos/shared.md",
        )
        if rename:
            subprocess.run(["git", "mv", "old.md", "new.md"], cwd=repo, check=True)
        else:
            (repo / source).write_text("completed sibling transition\n", encoding="utf-8")
        commit_all(repo, "sibling work")
        if sibling_status == "done":
            code, result = run("finish", sibling, "--use-committed-diff")
            assert code == 0, result
        elif sibling_status == "blocked":
            assert run("block", sibling, "--reason", "Waiting on sibling acceptance")[0] == 0
    if current_after_sibling:
        start_current()
    return repo, parent, current, sibling, run


def _receipt_path(root: Path, task_id: str) -> Path:
    return root / f"docs/tasks/.repoctl-state/completions/{task_id}.json"


def _authority_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (root / "docs").rglob("*")
        if path.is_file() and "debug" not in path.parts
    }


def _assert_rejected(
    root: Path, run, current: str, *selectors: str, codes: set[str] | None = None,
) -> None:
    before = _authority_snapshot(root)
    options = [part for selector in selectors for part in ("--acknowledge-completed", selector)]
    for command in ("doctor", "finish"):
        code, result = run(command, current, "--use-committed-diff", *options)
        assert code != 0, result
        assert result["ok"] is False, result
        if codes:
            assert codes & {problem["code"] for problem in result["problems"]}, result
    assert _authority_snapshot(root) == before
    assert not _receipt_path(root, current).exists()


def test_path_scoped_rename_requires_complete_edge_and_preserves_receipt(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    repo, _parent, current, sibling, run = _committed_sibling_fixture(
        tmp_path, monkeypatch, capsys, rename=True,
    )
    sibling_receipt = _receipt_path(tmp_path, sibling)
    original = sibling_receipt.read_bytes()
    receipt = json.loads(original)
    assert any(
        entry.get("old_path") == "old.md" and entry["path"] == "new.md"
        for entry in receipt["changed_entries"]
    )
    assert {item["path"] for item in receipt["repo_evidence"]["path_transitions"]} == {
        "old.md", "new.md",
    }
    for path in ("old.md", "new.md"):
        _assert_rejected(
            tmp_path, run, current, f"{sibling}:{path}",
            codes={"completion_acknowledgement_invalid"},
        )

    current_baseline = tmp_path / f"docs/tasks/.repoctl-state/{current}.json"
    baseline_bytes = current_baseline.read_bytes()
    sibling_task = tmp_path / f"docs/tasks/{sibling}--child-2.md"
    sibling_task_bytes = sibling_task.read_bytes()
    product_before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo)
    # Unsorted duplicate selectors are normalized without changing the receipt.
    options = (
        "--acknowledge-completed", f"{sibling}:old.md",
        "--acknowledge-completed", f"{sibling}:new.md",
        "--acknowledge-completed", f"{sibling}:old.md",
    )
    expected = [{
        "task_id": sibling,
        "receipt_path": sibling_receipt.relative_to(tmp_path).as_posix(),
        "receipt_sha256": "sha256:" + hashlib.sha256(original).hexdigest(),
        "selected_paths": ["new.md", "old.md"],
    }]
    code, doctor = run("doctor", current, "--use-committed-diff", *options)
    assert code == 0 and doctor["data"]["finish_ready"] is True, doctor
    assert doctor["data"]["acknowledged_completions"] == expected
    code, finished = run("finish", current, "--use-committed-diff", *options)
    assert code == 0, finished
    assert finished["data"]["acknowledged_completions"] == expected
    current_receipt = json.loads(_receipt_path(tmp_path, current).read_text())
    assert {entry["path"] for entry in current_receipt["changed_entries"]} == {"app.py"}
    assert {item["path"] for item in current_receipt["repo_evidence"]["path_transitions"]} == {"app.py"}
    assert sibling_receipt.read_bytes() == original
    assert sibling_task.read_bytes() == sibling_task_bytes
    assert current_baseline.read_bytes() == baseline_bytes
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo) == product_before
    assert subprocess.check_output(["git", "status", "--porcelain=v2"], cwd=repo) == b""


@pytest.mark.parametrize("pending_path", ["old.md", "new.md"])
def test_path_scoped_rename_rejects_pending_edit_on_either_end(
    tmp_path: Path, monkeypatch, capsys, pending_path: str,
) -> None:
    repo, _parent, current, sibling, run = _committed_sibling_fixture(
        tmp_path, monkeypatch, capsys, rename=True,
    )
    (repo / pending_path).write_text("pending work must remain owned here\n", encoding="utf-8")
    if pending_path == "new.md":
        subprocess.run(["git", "add", "new.md"], cwd=repo, check=True)
    product_before = subprocess.check_output(["git", "status", "--porcelain=v2"], cwd=repo)
    _assert_rejected(
        tmp_path, run, current, f"{sibling}:old.md", f"{sibling}:new.md",
        codes={"completion_acknowledgement_invalid"},
    )
    assert subprocess.check_output(["git", "status", "--porcelain=v2"], cwd=repo) == product_before


@pytest.mark.parametrize("position", ["before-sibling", "after-sibling", "after-sibling-restored", "before-current-start"])
def test_path_scoped_ack_rejects_unrelated_committed_lineage(
    tmp_path: Path, monkeypatch, capsys, position: str,
) -> None:
    repo, _parent, current, sibling, run = _committed_sibling_fixture(
        tmp_path, monkeypatch, capsys,
        prior_change=position == "before-sibling",
        current_after_sibling=position == "before-current-start",
    )
    if position.startswith("after-sibling"):
        completed_bytes = (repo / "shared.md").read_bytes()
        (repo / "shared.md").write_text("unrelated later transition\n", encoding="utf-8")
        commit_all(repo, "unrelated later work")
        if position == "after-sibling-restored":
            (repo / "shared.md").write_bytes(completed_bytes)
            commit_all(repo, "later work restored sibling endpoint bytes")
    product_before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo)
    _assert_rejected(
        tmp_path, run, current, f"{sibling}:shared.md",
        codes={"transition_lineage_gap", "terminal_evidence_drift", "completion_acknowledgement_invalid"},
    )
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo) == product_before


@pytest.mark.parametrize("status", ["todo", "doing", "blocked"])
def test_path_scoped_ack_rejects_live_sibling(
    tmp_path: Path, monkeypatch, capsys, status: str,
) -> None:
    _repo, _parent, current, sibling, run = _committed_sibling_fixture(
        tmp_path, monkeypatch, capsys, sibling_status=status,
    )
    _assert_rejected(
        tmp_path, run, current, f"{sibling}:shared.md",
        codes={"completion_acknowledgement_invalid"},
    )


@pytest.mark.parametrize("damage", ["missing", "identity", "artifact-hash", "fingerprint-hash", "transition", "missing-transitions"])
def test_path_scoped_ack_validates_entire_immutable_receipt(
    tmp_path: Path, monkeypatch, capsys, damage: str,
) -> None:
    _repo, _parent, current, sibling, run = _committed_sibling_fixture(
        tmp_path, monkeypatch, capsys,
    )
    receipt_path = _receipt_path(tmp_path, sibling)
    receipt = json.loads(receipt_path.read_text())
    if damage == "missing":
        receipt_path.unlink()
    else:
        if damage == "identity":
            receipt["task_id"] = current
        elif damage == "artifact-hash":
            sibling_task = tmp_path / f"docs/tasks/{sibling}--child-2.md"
            sibling_task.write_bytes(sibling_task.read_bytes() + b"\nUnrecorded history edit.\n")
        elif damage == "fingerprint-hash":
            receipt["repo_evidence"]["diff_fingerprint_sha256"] = "sha256:" + "0" * 64
        elif damage == "transition":
            receipt["repo_evidence"]["path_transitions"][0]["after"] = {"kind": "missing"}
        else:
            receipt["repo_evidence"]["path_transitions"] = []
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    _assert_rejected(
        tmp_path, run, current, f"{sibling}:shared.md",
        codes={"completion_acknowledgement_invalid", "child_completion_receipt_invalid"},
    )


@pytest.mark.parametrize("selector_path", ["/shared.md", "../shared.md", "./shared.md", "missing.md", "untouched.md"])
def test_path_scoped_ack_rejects_noncanonical_or_unreceipted_path(
    tmp_path: Path, monkeypatch, capsys, selector_path: str,
) -> None:
    _repo, _parent, current, sibling, run = _committed_sibling_fixture(
        tmp_path, monkeypatch, capsys,
    )
    _assert_rejected(
        tmp_path, run, current, f"{sibling}:{selector_path}",
        codes={"completion_acknowledgement_invalid"},
    )


def test_path_scoped_ack_requires_recorded_baseline(tmp_path: Path, monkeypatch, capsys) -> None:
    _repo, _parent, current, sibling, run = _committed_sibling_fixture(
        tmp_path, monkeypatch, capsys,
    )
    (tmp_path / f"docs/tasks/.repoctl-state/{current}.json").unlink()
    _assert_rejected(tmp_path, run, current, f"{sibling}:shared.md")


def test_path_scoped_ack_rejects_rewritten_product_history(tmp_path: Path, monkeypatch, capsys) -> None:
    repo, _parent, current, sibling, run = _committed_sibling_fixture(
        tmp_path, monkeypatch, capsys,
    )
    # Only this synthetic repository is rewritten; original Task evidence is retained.
    subprocess.run(
        ["git", "checkout", "--orphan", "unrelated-history"], cwd=repo,
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    commit_all(repo, "new unrelated root")
    _assert_rejected(tmp_path, run, current, f"{sibling}:shared.md")


def test_acknowledgement_modes_cannot_mix_across_siblings(tmp_path: Path, monkeypatch, capsys) -> None:
    repo, parent, current, sibling, run = _committed_sibling_fixture(
        tmp_path, monkeypatch, capsys,
    )
    second = "T-20260609184249Z"
    add_task(tmp_path, f"{second}--second-sibling.md", _repo_child_text(second, parent=parent))
    board = tmp_path / "docs/BOARD.md"
    board.write_text(board.read_text().replace(
        "\n## Backlog", f"\n- docs/tasks/{second}--second-sibling.md\n\n## Backlog",
    ))
    assert run("start", second)[0] == 0
    record_discovery(tmp_path, second, chosen="repos/second.md")
    (repo / "second.md").write_text("another sibling's independent work\n", encoding="utf-8")
    commit_all(repo, "second sibling work")
    assert run("finish", second, "--use-committed-diff")[0] == 0
    # Both whole-receipt siblings are disjoint and otherwise valid together.
    code, whole = run(
        "doctor", current, "--use-committed-diff",
        "--acknowledge-completed", sibling, "--acknowledge-completed", second,
    )
    assert code == 0 and whole["data"]["finish_ready"] is True, whole
    assert all("selected_paths" not in item for item in whole["data"]["acknowledged_completions"])
    _assert_rejected(
        tmp_path, run, current, sibling, f"{second}:second.md",
        codes={"completion_acknowledgement_invalid"},
    )
    _assert_rejected(
        tmp_path, run, current, f"{sibling}:shared.md", second,
        codes={"completion_acknowledgement_invalid"},
    )


def test_path_scoped_ack_rejects_other_parent(tmp_path: Path, monkeypatch, capsys) -> None:
    parent, current, sibling, other_parent = [f"T-2026060918434{i}Z" for i in range(6, 10)]
    repo = _parent_child_repo_fixture(tmp_path, parent, [current, sibling])
    add_task(tmp_path, f"{other_parent}--other-parent.md", task_text(other_parent))
    sibling_path = tmp_path / f"docs/tasks/{sibling}--child-2.md"
    sibling_path.write_text(sibling_path.read_text().replace(
        f'parent: "{parent}"', f'parent: "{other_parent}"',
    ))
    board = tmp_path / "docs/BOARD.md"
    board.write_text(board.read_text().replace(
        "\n## Backlog", f"\n- docs/tasks/{other_parent}--other-parent.md\n\n## Backlog",
    ))
    (repo / "shared.md").write_text("baseline\n", encoding="utf-8")
    commit_all(repo, "shared baseline")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    def run(*args: str) -> tuple[int, dict]:
        code = main(["task", *args, "--json"])
        return code, json.loads(capsys.readouterr().out)

    assert run("start", current)[0] == 0
    record_discovery(tmp_path, current, chosen="repos/app.py")
    assert run("start", sibling)[0] == 0
    record_discovery(tmp_path, sibling, chosen="repos/shared.md")
    (repo / "shared.md").write_text("sibling work\n", encoding="utf-8")
    commit_all(repo, "sibling under another parent")
    assert run("finish", sibling, "--use-committed-diff")[0] == 0
    _assert_rejected(
        tmp_path, run, current, f"{sibling}:shared.md",
        codes={"completion_acknowledgement_invalid"},
    )


def test_path_scoped_ack_rejects_other_repository(tmp_path: Path, monkeypatch, capsys) -> None:
    parent, current, sibling = [f"T-2026060918444{i}Z" for i in range(6, 9)]
    write_workspace(tmp_path)
    (tmp_path / "repos").mkdir()
    add_task(tmp_path, f"{parent}--parent.md", task_text(parent))
    add_task(tmp_path, f"{current}--current.md", _repo_child_text(current, parent=parent))
    add_task(tmp_path, f"{sibling}--sibling.md", _repo_child_text(sibling, parent=parent).replace(
        'repo_id: "main"', 'repo_id: "other"',
    ))
    (tmp_path / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n"
        f"- docs/tasks/{parent}--parent.md\n"
        f"- docs/tasks/{current}--current.md\n"
        f"- docs/tasks/{sibling}--sibling.md\n\n## Backlog\n",
    )
    for repo_id in ("main", "other"):
        init_committed_product_repo(tmp_path / f"repos/{repo_id}", {"shared.md": "base\n"})
    write_settings(tmp_path, {"repositories": [
        {"id": repo_id, "path": f"repos/{repo_id}"} for repo_id in ("main", "other")
    ]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    def run(*args: str) -> tuple[int, dict]:
        code = main(["task", *args, "--json"])
        return code, json.loads(capsys.readouterr().out)

    assert run("start", current)[0] == 0
    record_discovery(tmp_path, current, chosen="repos/main/shared.md")
    assert run("start", sibling)[0] == 0
    record_discovery(tmp_path, sibling, chosen="repos/other/shared.md")
    other = tmp_path / "repos/other"
    (other / "shared.md").write_text("other repository's work\n", encoding="utf-8")
    commit_all(other, "other repository sibling")
    assert run("finish", sibling, "--use-committed-diff")[0] == 0
    _assert_rejected(
        tmp_path, run, current, f"{sibling}:shared.md",
        codes={"completion_acknowledgement_invalid"},
    )



def test_path_scoped_ack_rejects_sibling_commit_outside_current_ancestry(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    repo, _parent, current, sibling, run = _committed_sibling_fixture(
        tmp_path, monkeypatch, capsys,
    )
    receipt = json.loads(_receipt_path(tmp_path, sibling).read_text())
    sibling_start = receipt["repo_evidence"]["start_head"]
    sibling_terminal = receipt["repo_evidence"]["observed_head"]
    completed_bytes = (repo / "shared.md").read_bytes()
    # Preserve the completed sibling's branch. This alternate branch still
    # descends from current's start, but its identical bytes are independent work.
    subprocess.run(
        ["git", "checkout", "-b", "independent-current-work", sibling_start],
        cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    (repo / "shared.md").write_bytes(completed_bytes)
    commit_all(repo, "independent work with matching endpoint bytes")
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", sibling_terminal, "HEAD"],
        cwd=repo, check=False,
    ).returncode == 1
    _assert_rejected(
        tmp_path, run, current, f"{sibling}:shared.md",
        codes={"completion_acknowledgement_invalid", "transition_lineage_gap"},
    )
