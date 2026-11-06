from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.repoctl.task_lifecycle_helpers import add_board_task, task_text, write_workspace
from tools.repoctl.cli import main
from tools.repoctl.markdown import replace_section

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/takeover-drift-benchmark/cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _invoke(capsys, argv: list[str]) -> tuple[int, dict]:
    code = main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def _prepare_case(root: Path, case: dict) -> Path:
    write_workspace(root)
    for key, content in (
        ("requirement_path", "requirement_original"),
        ("supporting_path", "supporting_original"),
    ):
        path = root / case[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(case[content], encoding="utf-8")
    unrelated = root / case["unrelated_path"]
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("Unrelated planning note.\n", encoding="utf-8")

    task_id = case["task_id"]
    record = task_text(task_id, status="doing")
    record = record.replace(f'title: "Task {task_id}"', f'title: "{case["title"]}"')
    record = record.replace('area: ""', f'area: "{case["area"]}"')
    record = record.replace("## Discovery", (
        "## Goal\n\n" + case["goal"] + "\n\n"
        "## Context Docs\n\n"
        "- Requirement: `" + case["requirement_path"] + "`\n"
        "- Supporting: `" + case["supporting_path"] + "`\n\n"
        "## Discovery"
    ), 1)
    record = replace_section(record, "Handoff", (
        "- Next exact step: Review the authoritative requirement against the incident evidence.\n"
        "- First file to open: `" + case["requirement_path"] + "`\n"
        "- First command to run: `./scripts/repoctl task show " + task_id + " --json`\n"
        "- Done when: The authored requirement and observed incident no longer disagree.\n"
    ))
    return add_board_task(root, f"{task_id}--takeover.md", record)


@pytest.mark.parametrize("case", CASES, ids=[item["id"] for item in CASES])
def test_natural_takeover_and_document_drift_benchmark(
    tmp_path: Path, monkeypatch, capsys, case: dict,
) -> None:
    """Two natural-language cases; measure first action, drift recall, and false positives."""
    task_path = _prepare_case(tmp_path, case)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    task_id = case["task_id"]

    code, bound = _invoke(capsys, ["task", "handoff", "bind", task_id])
    assert code == 0, bound
    original_record = task_path.read_bytes()
    code, resumed = _invoke(capsys, ["task", "resume", task_id])
    assert code == 0, resumed
    guidance = resumed["data"]["resume_guidance"]
    assert guidance["status"] == "current"
    assert [value.strip("`") for value in guidance["executable_handoff"]["First file to open"]] == [
        case["requirement_path"]
    ]
    assert any("Review the authoritative requirement" in value
               for value in guidance["executable_handoff"]["Next exact step"])
    assert not guidance["handoff"]["context_doc_changes"]

    unrelated = tmp_path / case["unrelated_path"]
    unrelated.write_text("Changed unrelated roadmap draft.\n", encoding="utf-8")
    code, unaffected = _invoke(capsys, ["task", "resume", task_id])
    assert code == 0, unaffected
    assert unaffected["data"]["resume_guidance"]["status"] == "current"
    assert not any(warning["code"] == "task_context_docs_changed" for warning in unaffected["warnings"])
    assert task_path.read_bytes() == original_record  # the query does not rewrite the Task

    supporting = tmp_path / case["supporting_path"]
    supporting.write_text(case["supporting_changed"], encoding="utf-8")
    code, stale = _invoke(capsys, ["task", "resume", task_id])
    assert code == 0, stale
    guidance = stale["data"]["resume_guidance"]
    assert guidance["status"] == "inactive"
    assert guidance["executable_handoff"] is None
    changes = guidance["handoff"]["context_doc_changes"]
    assert len(changes) == 1
    assert (changes[0]["path"], changes[0]["role"], changes[0]["change"]) == (
        case["supporting_path"], "supporting", "changed"
    )

    requirement = tmp_path / case["requirement_path"]
    requirement.write_text(case["requirement_changed"], encoding="utf-8")
    code, stale = _invoke(capsys, ["task", "resume", task_id])
    assert code == 0, stale
    changes = stale["data"]["resume_guidance"]["handoff"]["context_doc_changes"]
    assert {(change["path"], change["role"], change["change"]) for change in changes} == {
        (case["requirement_path"], "requirement", "changed"),
        (case["supporting_path"], "supporting", "changed"),
    }
    assert stale["next_actions"][0]["kind"] == "context_doc_review"
    assert stale["next_actions"][0]["priority"] == "required"

    code, rebound = _invoke(capsys, ["task", "handoff", "bind", task_id])
    assert code == 0, rebound
    code, current = _invoke(capsys, ["task", "resume", task_id])
    assert code == 0, current
    assert current["data"]["resume_guidance"]["status"] == "current"

    supporting.rename(supporting.with_name("renamed-incident.md"))
    code, missing = _invoke(capsys, ["task", "resume", task_id])
    assert code == 0, missing
    evidence = missing["data"]["resume_guidance"]["handoff"]["context_doc_changes"]
    assert any(item["path"] == case["supporting_path"] and item["change"] == "missing"
               for item in evidence)
    assert missing["data"]["resume_guidance"]["status"] == "inactive"
    assert task_path.read_bytes() == original_record
