from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.repoctl.task_lifecycle_helpers import add_board_task, task_text, write_workspace
from tools.repoctl.cli import main
from tools.repoctl.markdown import replace_section
from tools.repoctl.tasks import load_task, task_content_audit


TASK_ID = "T-20260609184046Z"


def _audit_handoff(root: Path, handoff: str) -> dict:
    path = root / "docs/tasks" / f"{TASK_ID}--owner.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(replace_section(task_text(TASK_ID), "Handoff", handoff), encoding="utf-8")
    return task_content_audit(load_task(path, root))


@pytest.mark.parametrize(
    "instruction",
    [
        "Review the audited plan with the user. Do not resume any child provider/collector or device work.",
        "현재 문서·task 계획을 검토한다. 과거 task의 모델/기기/collector/팀 작업은 재개하지 않는다.",
        "별도 실행 승인을 기다린다. 승인 후 ACK/저장/adapter 대기를 대조한다.",
        "세 독립 노드에서 동일 signed Event/Blob·저장 ACK/앱 ACK와 재시작·중단·중복 접촉을 증명한다.",
        "승인 후 Play 테스트 구매의 성공·pending·취소·ack·복원·환불·오프라인 사례를 검증한다.",
        "Review artifacts/repoctl-path-ack.patch before rerunning the official scoped doctor.",
        "This decision rejects an ACK queue and external completion collector; preserve the original request deadline.",
    ],
    ids=["english-constraint", "korean-constraint", "approval-boundary", "transport-ack", "billing-ack", "patch-name", "negated-protocol"],
)
def test_prospective_restart_instructions_are_not_operational_history(tmp_path: Path, instruction: str) -> None:
    audit = _audit_handoff(
        tmp_path,
        f"- Next exact step: {instruction}\n"
        "- First file to open: docs/PRD.md\n"
        "- First command to run: git status --short\n"
        "- Done when: The current acceptance evidence has been reviewed.\n",
    )
    assert audit["findings"] == []


@pytest.mark.parametrize(
    "label",
    ["ACK", "collector", "request queue", "role transition", "external completion", "deleted file hash"],
)
def test_state_records_inside_restart_fields_still_warn(tmp_path: Path, label: str) -> None:
    audit = _audit_handoff(tmp_path, f"- Next exact step: {label}: request-17 recorded\n")
    assert [(item["code"], item["count"]) for item in audit["findings"]] == [
        ("task_handoff_operational_history", 1),
    ]


def test_timestamped_and_nested_history_records_still_warn(tmp_path: Path) -> None:
    audit = _audit_handoff(
        tmp_path,
        "- Next exact step: Review the remaining acceptance evidence.\n"
        "  - 20261110T021500Z: ACK received for request-17\n"
        "  - [2026-11-10T02:16:00Z] collector completed request-17\n"
        "- Done when: role transition: worker -> collector recorded\n",
    )
    assert [(item["code"], item["count"]) for item in audit["findings"]] == [
        ("task_handoff_operational_history", 3),
    ]


def test_code_examples_do_not_hide_following_real_history(tmp_path: Path) -> None:
    audit = _audit_handoff(
        tmp_path,
        "- Next exact step: Review `ACK: received` and ``collector: `sample` value`` examples.\n"
        "  ````text\n"
        "  ACK: quoted example\n"
        "  ```\n"
        "  collector: still inside the longer fence\n"
        "  ````\n"
        "~~~text\n"
        "20261110T021500Z: ACK quoted example\n"
        "~~~\n"
        "- Done when: Current acceptance is verified.\n"
        "- collector: completed request-17\n",
    )
    assert [(item["code"], item["count"]) for item in audit["findings"]] == [
        ("task_handoff_operational_history", 1),
    ]


def test_first_command_is_inert_without_exempting_other_restart_fields(tmp_path: Path) -> None:
    audit = _audit_handoff(
        tmp_path,
        '- First command to run: rg -n "ACK:|collector|request queue" src\n'
        "  ACK: this line is part of the indented command value\n"
        "- Next exact step: ACK: request-17 accepted\n",
    )
    assert [(item["code"], item["count"]) for item in audit["findings"]] == [
        ("task_handoff_operational_history", 1),
    ]


def test_history_audit_is_read_only_warning_only_for_live_and_archived_tasks(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = replace_section(task_text(TASK_ID), "Handoff", "- Next exact step: ACK: request-17 accepted\n")
    add_board_task(tmp_path, f"{TASK_ID}--owner.md", text)
    archived_id = "T-20260609184047Z"
    archived = tmp_path / "docs/archive/tasks" / f"{archived_id}--closed.md"
    archived.parent.mkdir(parents=True, exist_ok=True)
    closed_text = replace_section(
        task_text(archived_id, status="done"), "Handoff", "- Next exact step: collector: request-18 completed\n",
    ).replace("## Handoff", "## Last Active Handoff")
    archived.write_text(closed_text, encoding="utf-8")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "audit", "--include-archived", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["problems"] == []
    assert result["data"]["finding_count"] == 2
    assert {task["task_id"] for task in result["data"]["tasks"] if task["findings"]} == {TASK_ID, archived_id}
    assert all(item["severity"] == "warning" for task in result["data"]["tasks"] for item in task["findings"])
    assert {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("source_bytes", [50 * 1024, 50 * 1024 + 1])
def test_task_source_size_review_threshold_remains_50_kib(tmp_path: Path, source_bytes: int) -> None:
    path = tmp_path / f"{TASK_ID}--owner.md"
    text = task_text(TASK_ID) + "\n## Authored Analysis\n\n"
    text += "x" * (source_bytes - len(text.encode("utf-8")))
    path.write_text(text, encoding="utf-8")

    audit = task_content_audit(load_task(path, tmp_path))
    assert audit["bytes"] == source_bytes
    assert [item["code"] for item in audit["findings"]] == (
        ["task_source_large"] if source_bytes > 50 * 1024 else []
    )
    assert path.read_text(encoding="utf-8") == text
