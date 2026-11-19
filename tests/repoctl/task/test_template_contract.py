from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.repoctl.workspace.test_check import add_task, run_json, snapshot, task_text, write_workspace
from tools.repoctl.template_contract import TEMPLATE_CONTRACT_SCHEMA


@pytest.mark.parametrize("version", [None, "1.8.3", "2.0.0"])
def test_unversioned_and_supported_contracts_read_and_finish_shared_task_fields(tmp_path: Path, capsys, version: str | None) -> None:
    write_workspace(tmp_path)
    marker = tmp_path / "docs/template-contract.json"
    if version is not None:
        marker.write_text(json.dumps({"schema": TEMPLATE_CONTRACT_SCHEMA, "version": version}), encoding="utf-8")
    path = add_task(tmp_path, "task.md", task_text("one"))
    if version == "2.0.0":
        fixture = Path(__file__).resolve().parents[2] / "fixtures/template-contract-2.0.0/fixtures/conformance/valid-task.md"
        path.write_bytes(fixture.read_bytes())
        task_id = "T-20260609184046Z"
    else:
        task_id = "one"
    before = snapshot(tmp_path)

    code, checked = run_json(tmp_path, capsys, "check")
    assert code == 0
    assert checked["data"]["template_contract"] == {
        "status": "supported" if version else "legacy_unversioned",
        "version": version or "",
    }
    code, resumed = run_json(tmp_path, capsys, "task", "resume", task_id)
    assert code == 0
    assert resumed["data"]["task"]["id"] == task_id
    assert resumed["data"]["handoff"]
    assert snapshot(tmp_path) == before
    assert run_json(tmp_path, capsys, "task", "start", task_id)[0] == 0
    assert run_json(tmp_path, capsys, "task", "finish", task_id)[0] == 0
    if version is not None:
        assert marker.read_bytes() == before["docs/template-contract.json"]


@pytest.mark.parametrize("contents,expected", [
    ("not JSON", "template_contract_invalid"),
    ("[]", "template_contract_invalid"),
    (json.dumps({"schema": TEMPLATE_CONTRACT_SCHEMA, "version": "2"}), "template_contract_invalid"),
    (json.dumps({"schema": TEMPLATE_CONTRACT_SCHEMA, "version": "3.0.0"}), "template_contract_unsupported"),
])
def test_check_diagnoses_contract_marker_without_rewriting_it(tmp_path: Path, capsys, contents: str, expected: str) -> None:
    write_workspace(tmp_path)
    marker = tmp_path / "docs/template-contract.json"
    marker.write_text(contents, encoding="utf-8")
    before = snapshot(tmp_path)

    code, checked = run_json(tmp_path, capsys, "check")
    assert code == 2
    assert checked["problems"][0]["code"] == expected
    assert snapshot(tmp_path) == before


def test_contract_diagnostic_does_not_prevent_reading_or_updating_an_existing_task(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    marker = tmp_path / "docs/template-contract.json"
    marker.write_text('{"schema": "unrecognized", "version": "99.0.0"}', encoding="utf-8")
    add_task(tmp_path, "task.md", task_text("one"))
    before = marker.read_bytes()

    assert run_json(tmp_path, capsys, "task", "resume", "one")[0] == 0
    assert run_json(tmp_path, capsys, "task", "start", "one")[0] == 0
    assert run_json(tmp_path, capsys, "task", "finish", "one")[0] == 0
    assert marker.read_bytes() == before


def test_duplicate_handoff_conformance_fixture_reports_ambiguity(tmp_path: Path, capsys) -> None:
    write_workspace(tmp_path)
    fixture = Path(__file__).resolve().parents[2] / "fixtures/template-contract-2.0.0/fixtures/conformance/duplicate-handoff-task.md"
    source = add_task(tmp_path, "task.md", fixture.read_text(encoding="utf-8"))
    before = source.read_bytes()

    code, resumed = run_json(tmp_path, capsys, "task", "resume", "docs/tasks/task.md")
    assert code == 2
    assert resumed["problems"][0]["code"] == "duplicate_section"
    assert source.read_bytes() == before
