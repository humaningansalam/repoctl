from __future__ import annotations

from pathlib import Path

from tools.repoctl import knowledge_records as records


def _ref(value: str) -> dict[str, str]:
    return {
        "kind": "authority_document",
        "path": "docs/contracts/original.md",
        "content_sha256": records._sha256_text(value),
    }


def test_relocation_streaming_prunes_excluded_trees(tmp_path: Path, monkeypatch) -> None:
    directory = tmp_path / "docs/contracts"
    directory.mkdir(parents=True)
    expected = directory / "new-contract.md"
    expected.write_text("original exact evidence", encoding="utf-8")
    for name in (".git", "node_modules", "build", "dist", ".venv"):
        ignored = directory / name
        ignored.mkdir()
        for index in range(128):
            (ignored / f"unrelated-{index}.md").write_text("noise", encoding="utf-8")

    opened: list[Path] = []
    original = records.os.scandir

    def track(path):
        opened.append(Path(path))
        return original(path)

    monkeypatch.setattr(records.os, "scandir", track)
    result = records._relocated_source_resolution(
        tmp_path, repo_id="", ref=_ref("original exact evidence"),
    )

    assert result is not None
    assert result.status is records.KnowledgeSourceResolutionStatus.RELOCATED
    assert result.resolved_path == "docs/contracts/new-contract.md"
    assert all(not set(path.parts) & {".git", "node_modules", "build", "dist", ".venv"} for path in opened)
    assert result.scan_stats is not None
    assert result.scan_stats["visited_entries"] < 20
    assert result.scan_stats["hashed_files"] == 1


def test_relocation_entry_budget_stops_streaming_before_more_traversal(
    tmp_path: Path, monkeypatch,
) -> None:
    directory = tmp_path / "docs/contracts"
    directory.mkdir(parents=True)
    for index in range(80):
        (directory / f"unrelated-{index:03}.md").write_text("not a match", encoding="utf-8")
    (tmp_path / "docs/specs").mkdir()
    monkeypatch.setattr(records, "MAX_SOURCE_RELOCATION_VISITED_ENTRIES", 2)

    opened: list[Path] = []
    original = records.os.scandir

    def track(path):
        opened.append(Path(path))
        return original(path)

    monkeypatch.setattr(records.os, "scandir", track)
    result = records._relocated_source_resolution(tmp_path, repo_id="", ref=_ref("missing"))

    assert result is not None
    assert result.status is records.KnowledgeSourceResolutionStatus.INVALID_IDENTITY
    assert result.cause_code == "source_relocation_entry_limit"
    assert result.scan_stats is not None
    assert result.scan_stats["visited_entries"] == 3
    assert result.scan_stats["candidate_files"] <= 2
    assert opened == [directory]


def test_relocation_unique_digest_and_ambiguous_identity(tmp_path: Path) -> None:
    directory = tmp_path / "docs/contracts"
    directory.mkdir(parents=True)
    (directory / "moved.md").write_text("same evidence", encoding="utf-8")
    original = records._relocated_source_resolution(tmp_path, repo_id="", ref=_ref("same evidence"))
    assert original is not None
    assert original.status is records.KnowledgeSourceResolutionStatus.RELOCATED

    (directory / "also-moved.md").write_text("same evidence", encoding="utf-8")
    ambiguous = records._relocated_source_resolution(tmp_path, repo_id="", ref=_ref("same evidence"))
    assert ambiguous is not None
    assert ambiguous.status is records.KnowledgeSourceResolutionStatus.INVALID_IDENTITY
    assert ambiguous.cause_code == "source_relocation_ambiguous"


def test_relocation_hash_budget_reports_incomplete_without_claiming_unique(
    tmp_path: Path, monkeypatch,
) -> None:
    directory = tmp_path / "docs/contracts"
    directory.mkdir(parents=True)
    (directory / "moved.md").write_text("same evidence", encoding="utf-8")
    monkeypatch.setattr(records, "MAX_SOURCE_RELOCATION_HASH_BYTES", 4)
    result = records._relocated_source_resolution(tmp_path, repo_id="", ref=_ref("same evidence"))

    assert result is not None
    assert result.status is records.KnowledgeSourceResolutionStatus.INVALID_IDENTITY
    assert result.cause_code == "source_relocation_byte_limit"
    assert result.scan_stats is not None
    assert result.scan_stats["hashed_bytes"] <= 4
