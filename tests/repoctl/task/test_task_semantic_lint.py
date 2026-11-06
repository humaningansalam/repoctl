from __future__ import annotations

import json
from pathlib import Path

from tests.repoctl.task_lifecycle_helpers import add_board_task, task_text, write_workspace
from tools.repoctl.cli import main
from tools.repoctl.markdown import replace_section
from tools.repoctl.task_semantic_lint import semantic_log_repetition


def _repeated_log(count: int = 35) -> str:
    return "\n".join(
        f"- 20260609T{index:06}Z: Checked status again; no change; will retry later."
        for index in range(count)
    )


def test_repeated_no_change_checkpoints_are_warning_only(tmp_path: Path, monkeypatch, capsys) -> None:
    assert semantic_log_repetition(_repeated_log())[0]["count"] == 35
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    text = replace_section(task_text(task_id), "Execution Log", _repeated_log() + "\n")
    add_board_task(tmp_path, f"{task_id}--owner.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "audit", task_id, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    findings = result["data"]["tasks"][0]["findings"]
    repeat = next(item for item in findings if item["code"] == "task_log_semantic_repetition")
    assert repeat["severity"] == "warning"
    assert repeat["count"] == 35
    assert repeat["sample_line"] == 1
    assert result["problems"] == []


def test_authored_incident_timelines_and_fenced_examples_do_not_trigger() -> None:
    repeated = _repeated_log()
    fenced = "```text\n" + repeated + "\n```"
    assert semantic_log_repetition(fenced) == []

    causes = [
        "login lockout", "expired certificate", "database failover",
        "network partition", "cache stampede", "clock skew",
        "schema migration", "permission policy", "expired token",
        "rate limit", "stale replication", "disk exhaustion",
    ]
    real_timeline = "\n".join(
        f"- 20260609T{index:06}Z: Investigated the incident; identified {cause}; updated response and evidence."
        for index, cause in enumerate(causes * 2)
    )
    assert semantic_log_repetition(real_timeline) == []

    distinct_mixed = "\n".join(
        f"- {index}: 검토 결과 {cause} 원인과 수정을 기록했습니다; verification preserved."
        for index, cause in enumerate(causes * 2)
    )
    assert semantic_log_repetition(distinct_mixed) == []


def test_long_design_decision_and_negated_keywords_are_not_scored_by_length() -> None:
    detailed_decision = (
        "- This design decision explicitly rejects an ACK queue and external completion collector; "
        "it preserves source authority, ownership, and rollback options. " * 60
    )
    assert semantic_log_repetition(detailed_decision) == []
    assert semantic_log_repetition("- 회의에서 기존 계획을 보류함\n- New evidence changed the cause and follow-up") == []
