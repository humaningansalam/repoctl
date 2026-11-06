from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.repoctl.cli import main
from tools.repoctl.task_context_docs import (
    MAX_DOCUMENT_BYTES,
    MAX_REFERENCES,
    context_doc_changes,
    context_doc_coverage,
    observe_context_docs,
    validate_context_doc_observation,
)
from tests.repoctl.task_lifecycle_helpers import add_board_task, task_text, write_workspace


TASK_PATH = "docs/tasks/T-20260609184046Z--alpha.md"


def _body(items: str) -> str:
    return "# Task\n\n## Context Docs\n\n" + items + "\n\n## Handoff\n\n- Next exact step: inspect\n"


@pytest.mark.parametrize("items", [
    "- Requirement: docs/specs/rule.md",
    "- Requirement: \x60docs/specs/rule.md#Rule\x60",
    "- Requirement: [Rule](/docs/specs/rule.md#Rule)",
    "* Requirement: [Rule](../specs/rule.md#Rule)",
    "1. Requirement: [Rule][source]\n\n[source]: ../specs/rule.md#Rule",
    "+ Requirement: [Rule](../specs/rule.md)",
])
def test_supported_reference_syntax_observes_full_file_digest(tmp_path: Path, items: str) -> None:
    doc = tmp_path / "docs/specs/rule.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Rule\nOriginal\n", encoding="utf-8")
    body = _body(items)
    before = observe_context_docs(tmp_path, body, task_path=TASK_PATH)
    assert validate_context_doc_observation(before)
    assert before["reference_count"] == 1
    assert before["documents"][0]["path"] == "docs/specs/rule.md"
    assert before["documents"][0]["status"] == "found"
    if "#Rule" in items:
        assert before["documents"][0]["section"] == "Rule"
    doc.write_text("# Rule\nChanged\n", encoding="utf-8")
    changed = context_doc_changes(before, observe_context_docs(tmp_path, body, task_path=TASK_PATH))
    assert [(item["change"], item["path"]) for item in changed] == [("changed", "docs/specs/rule.md")]


def test_fences_comments_and_unrelated_files_do_not_create_false_drift(tmp_path: Path) -> None:
    doc = tmp_path / "docs/specs/rule.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("Known\n", encoding="utf-8")
    other = tmp_path / "docs/specs/unrelated.md"
    other.write_text("Before\n", encoding="utf-8")
    fence = "\x60" * 3
    body = _body(
        f"- Requirement: docs/specs/rule.md\n{fence}text\n- Requirement: docs/specs/example.md\n{fence}\n"
        "<!-- - Requirement: docs/specs/commented.md -->"
    )
    before = observe_context_docs(tmp_path, body, task_path=TASK_PATH)
    assert before["reference_count"] == 1
    other.write_text("After\n", encoding="utf-8")
    assert context_doc_changes(before, observe_context_docs(tmp_path, body, task_path=TASK_PATH)) == []


def test_reference_budget_and_large_requirement_are_honestly_unobserved(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    lines = []
    for index in range(MAX_REFERENCES + 1):
        doc = docs / f"spec-{index}.md"
        doc.write_text(f"Version {index}\n", encoding="utf-8")
        lines.append(f"{index + 1}. Requirement: docs/spec-{index}.md")
    bounded = observe_context_docs(tmp_path, _body("\n".join(lines)), task_path=TASK_PATH)
    coverage = context_doc_coverage(bounded)
    assert coverage["observed_count"] == MAX_REFERENCES
    assert coverage["reference_count"] == MAX_REFERENCES + 1
    assert coverage["unobserved_count"] == 1
    assert coverage["required_unobserved"]
    assert coverage["incomplete"]
    oversized = docs / "huge.md"
    oversized.write_bytes(b"x" * (MAX_DOCUMENT_BYTES + 1))
    too_large = observe_context_docs(tmp_path, _body("- Requirement: docs/huge.md"), task_path=TASK_PATH)
    assert too_large["documents"][0]["status"] == "too_large"
    assert context_doc_coverage(too_large)["required_unobserved"]


def test_missing_symlink_traversal_ambiguity_external_and_unsupported(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    outside = tmp_path.parent / "outside-c1.md"
    # A link outside the workspace must never be followed or read.
    (docs / "external.md").symlink_to(outside)
    (docs / "root.md").write_text("Root\n", encoding="utf-8")
    alternate = tmp_path / "docs/tasks/docs/root.md"
    alternate.parent.mkdir(parents=True)
    alternate.write_text("Task relative\n", encoding="utf-8")
    body = _body(
        "- Requirement: docs/missing.md\n"
        "- Requirement: docs/external.md\n"
        "- Requirement: [Escape](../../../../outside-c1.md)\n"
        "- Requirement: [Ambiguous](docs/root.md)\n"
        "- Supporting: [External](https://example.com/terms#Section)\n"
        "- Supporting: [Unsupported](?invalid)"
    )
    results = observe_context_docs(tmp_path, body, task_path=TASK_PATH)
    statuses = {item["path"]: item["status"] for item in results["documents"]}
    assert statuses["docs/missing.md"] == "missing"
    assert statuses["docs/external.md"] == "invalid_path"
    assert statuses["../../outside-c1.md"] == "invalid_path"
    assert statuses["docs/root.md"] == "ambiguous_path"
    assert statuses["https://example.com/terms"] == "external_unobserved"
    assert statuses["?invalid"] == "unsupported_reference"
    assert context_doc_coverage(results)["required_unobserved"]
    assert validate_context_doc_observation(results)


def test_required_context_missing_at_bind_is_not_executable_in_default_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    authored = task_text(task_id, status="doing").replace(
        "## Verification\n", "## Context Docs\n\n- Requirement: [Rule](../specs/rule.md)\n\n## Verification\n",
    )
    add_board_task(tmp_path, f"{task_id}--alpha.md", authored)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "resume", "--json"]) == 0
    missing = json.loads(capsys.readouterr().out)
    guidance = missing["data"]["resume_guidance"]
    assert guidance["status"] == "inactive"
    assert guidance["executable_handoff"] is None
    assert "required_context_docs_unobserved" in guidance["handoff"]["reason_codes"]
    assert guidance["handoff"]["context_docs_coverage"]["issues"][0]["status"] == "missing"
    assert "task_context_docs_unobserved" in {item["code"] for item in missing["warnings"]}

    doc = tmp_path / "docs/specs/rule.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Rule\nVersion one\n", encoding="utf-8")
    assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "resume", "--json"]) == 0
    current = json.loads(capsys.readouterr().out)
    assert current["data"]["resume_guidance"]["status"] == "current"
    assert current["data"]["resume_guidance"]["executable_handoff"] is not None

    doc.write_text("# Rule\nVersion two\n", encoding="utf-8")
    assert main(["task", "resume", "--json"]) == 0
    changed = json.loads(capsys.readouterr().out)["data"]["resume_guidance"]
    assert changed["status"] == "inactive"
    assert changed["executable_handoff"] is None
    assert changed["handoff"]["context_doc_changes"][0]["change"] == "changed"
