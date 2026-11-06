from __future__ import annotations

from pathlib import Path

from tools.repoctl.task_context_docs import (
    MAX_REFERENCES,
    context_doc_changes,
    context_doc_paths,
    observe_context_docs,
    validate_context_doc_observation,
)


def _task_body(references: str) -> str:
    return f"# Example\n\n## Context Docs\n\n{references}\n\n## Handoff\n\n- Next exact step: inspect\n"


def test_changed_and_missing_reference_has_source_and_role_evidence(tmp_path: Path) -> None:
    doc = tmp_path / "docs/specs/auth.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Login\n\nOriginal rule.\n", encoding="utf-8")
    body = _task_body("- Requirement: `docs/specs/auth.md#Login`")
    before = observe_context_docs(tmp_path, body)
    assert validate_context_doc_observation(before)
    assert before["documents"][0]["status"] == "found"
    assert before["documents"][0]["section"] == "Login"

    doc.write_text("# Login\n\nRevised rule.\n", encoding="utf-8")
    changed = context_doc_changes(before, observe_context_docs(tmp_path, body))
    assert [(item["change"], item["role"], item["review_severity"]) for item in changed] == [
        ("changed", "requirement", "review_required"),
    ]
    assert changed[0]["before_digest"] != changed[0]["after_digest"]

    doc.unlink()
    missing = context_doc_changes(before, observe_context_docs(tmp_path, body))
    assert missing[0]["change"] == "missing"
    assert missing[0]["after_status"] == "missing"


def test_explicit_refs_only_and_traversal_or_symlink_rejected(tmp_path: Path) -> None:
    source = tmp_path / "docs/research/notes.md"
    source.parent.mkdir(parents=True)
    source.write_text("Known.", encoding="utf-8")
    unrelated = tmp_path / "docs/research/unrelated.md"
    unrelated.write_text("Before.", encoding="utf-8")
    body = _task_body(
        "- Supporting: `docs/research/notes.md`\n"
        "- `docs/../../escape.md`\n"
        "- `docs/research/link.md`\n"
        "<!-- - `docs/research/ignored.md` -->"
    )
    (tmp_path / "docs/research/link.md").symlink_to(source)
    before = observe_context_docs(tmp_path, body)
    assert context_doc_paths(body) == [
        "docs/research/notes.md", "docs/../../escape.md", "docs/research/link.md",
    ]
    assert [doc["status"] for doc in before["documents"]] == [
        "found", "invalid_path", "invalid_path",
    ]
    unrelated.write_text("After.", encoding="utf-8")
    assert context_doc_changes(before, observe_context_docs(tmp_path, body)) == []


def test_many_references_limit_reads_without_silent_completeness(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    body = _task_body("\n".join(
        f"- `docs/reference-{index}.md`"
        for index in range(MAX_REFERENCES + 3)
    ))
    result = observe_context_docs(tmp_path, body)
    assert result["reference_count"] == MAX_REFERENCES + 3
    assert result["truncated"] is True
    assert len(result["documents"]) == MAX_REFERENCES
    assert validate_context_doc_observation(result)
