from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.repoctl.knowledge_records import (
    add_knowledge_record,
    prepare_knowledge_record,
    query_knowledge_records,
    knowledge_status,
    show_knowledge_record,
    check_knowledge_records,
)
from tests.repoctl.repository.test_repositories import init_repo


def add(
    root: Path,
    claim: str,
    *,
    source: str = "repos/source.py",
    replaces: list[str] | None = None,
) -> dict:
    data, problems, warnings = add_knowledge_record(
        root,
        repo_id="main",
        kind="decision",
        claim=claim,
        reason="Explains the design choice",
        sources=[source],
        replaces=replaces,
    )
    assert not problems and not warnings
    return data["record"]


def test_claims_are_not_artificially_limited_and_sources_use_exact_bytes(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path / "repos")
    content = b"value = 42\r\n"
    (tmp_path / "repos/source.py").write_bytes(content)
    record = add(tmp_path, "설계 판단 " * 100)
    assert len(record["claim"]) > 300
    assert (
        record["source_refs"][0]["content_sha256"]
        == "sha256:" + hashlib.sha256(content).hexdigest()
    )
    data, problems, warnings = query_knowledge_records(
        tmp_path, repo_id="main", query="설계 판단"
    )
    assert not problems and not warnings
    assert data["results"][0]["record"]["source_resolutions"][0]["status"] == "current"
    assert not (tmp_path / ".repoctl-state/knowledge").exists()


def test_final_task_bytes_can_be_prepared_before_atomic_publication(
    tmp_path: Path,
) -> None:
    relative = "docs/archive/tasks/T-20261120120000Z-01234567--done.md"
    text = b"---\nid: T-20261120120000Z-01234567\nstatus: done\ncompleted_at: 2026-11-20T12:00:00Z\n---\n\nFinal result.\n"
    prepared, problems = prepare_knowledge_record(
        tmp_path,
        repo_id="",
        kind="invariant",
        claim="Result survives closeout",
        reason="Final Task document is the source",
        sources=[relative],
        source_bytes={relative: text},
    )
    assert not problems and prepared is not None
    assert (
        prepared.data["source_refs"][0]["content_sha256"]
        == "sha256:" + hashlib.sha256(text).hexdigest()
    )
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(text)
    prepared.path.parent.mkdir(parents=True)
    prepared.path.write_text(prepared.text)
    data, problems = check_knowledge_records(tmp_path, repo_id="")
    assert not problems
    assert data["records"][0]["source_resolutions"][0]["status"] == "current"
    path.write_bytes(text + b"Changed\n")
    data, problems = check_knowledge_records(tmp_path, repo_id="")
    assert data["records"][0]["source_resolutions"][0]["status"] == "digest_mismatch"
    assert problems[0].code == "knowledge_source_changed"


def test_query_observes_only_selected_sources_and_status_reads_records(
    tmp_path: Path, monkeypatch
) -> None:
    init_repo(tmp_path / "repos")
    for index in range(3):
        (tmp_path / f"repos/source{index}.py").write_text(f"value = {index}\n")
        add(tmp_path, f"decision{index}", source=f"repos/source{index}.py")
    original = Path.read_bytes
    observed: list[str] = []

    def read(path: Path) -> bytes:
        if path.parent == tmp_path / "repos":
            observed.append(path.name)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    assert knowledge_status(tmp_path, repo_id="main")["record_count"] == 3
    assert observed == []
    data, problems, warnings = query_knowledge_records(
        tmp_path, repo_id="main", query="decision1", limit=1
    )
    assert not problems and not warnings
    assert len(data["results"]) == 1
    assert observed == ["source1.py"]


def test_missing_source_is_reported_without_automatic_relocation(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path / "repos")
    original = tmp_path / "repos/source.py"
    original.write_text("value = 42\n")
    record = add(tmp_path, "Stable source location")
    original.rename(tmp_path / "repos/moved.py")
    data, problems = show_knowledge_record(
        tmp_path, repo_id="main", record_id=record["id"]
    )
    assert not problems
    resolution = data["record"]["source_resolutions"][0]
    assert resolution["status"] == "missing"
    assert resolution["resolved_path"] == "repos/source.py"


def test_replacement_excludes_superseded_record_from_default_query(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path / "repos")
    (tmp_path / "repos/source.py").write_text("value = 42\n")
    first = add(tmp_path, "original cache decision")
    second = add(tmp_path, "replacement cache decision", replaces=[first["id"]])
    data, _, _ = query_knowledge_records(tmp_path, repo_id="main", query="cache")
    assert [item["record"]["id"] for item in data["results"]] == [second["id"]]
    history, _, _ = query_knowledge_records(
        tmp_path, repo_id="main", query="cache", include_history=True
    )
    assert {item["record"]["status"] for item in history["results"]} == {
        "current",
        "superseded",
    }


def test_source_escape_is_rejected_even_with_supplied_bytes(tmp_path: Path) -> None:
    prepared, problems = prepare_knowledge_record(
        tmp_path,
        repo_id="",
        kind="decision",
        claim="No escape",
        reason="Source must be inside workspace",
        sources=["../external.md"],
        source_bytes={"../external.md": b"content"},
    )
    assert prepared is None and problems


def test_existing_historical_source_is_a_plain_file(tmp_path: Path) -> None:
    relative = "docs/archive/legacy/evidence.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"historical":true}\r\n')
    record, problems, _ = add_knowledge_record(
        tmp_path,
        repo_id="",
        kind="decision",
        claim="Historical decision",
        reason="Original source remains readable",
        sources=[relative],
    )
    assert not problems
    assert record["record"]["source_refs"][0]["kind"] == "workspace_file"
    data, problems = check_knowledge_records(tmp_path, repo_id="")
    assert (
        not problems
        and data["records"][0]["source_resolutions"][0]["status"] == "current"
    )


def test_repository_at_workspace_root_keeps_code_refs_and_task_provenance_distinct(
    tmp_path: Path,
) -> None:
    from tests.repoctl.repository.test_repositories import write_settings

    init_repo(tmp_path)
    write_settings(tmp_path, {"repositories": [{"id": "main", "path": "."}]})
    (tmp_path / "app.py").write_text("value = 1\n")
    task_path = tmp_path / "docs/archive/tasks/finished.md"
    task_path.parent.mkdir(parents=True)
    task_path.write_text("Completed Task\n")
    prepared, problems = prepare_knowledge_record(
        tmp_path,
        repo_id="main",
        kind="decision",
        claim="Root repository source",
        reason="Code is a code anchor and Task is provenance",
        sources=["./app.py", "docs/archive/tasks/finished.md"],
    )
    assert prepared is not None and not problems
    assert [ref["kind"] for ref in prepared.data["source_refs"]] == [
        "current_source",
        "task_artifact",
    ]


def test_malformed_source_references_are_reported_without_hiding_valid_records(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path / "repos")
    (tmp_path / "repos/source.py").write_text("value = 1\n")
    record = add(tmp_path, "valid decision")
    broken = {**record, "id": "broken", "source_refs": ["repos/source.py"]}
    path = tmp_path / "docs/knowledge/records/broken.json"
    path.write_text(json.dumps(broken))
    data, problems = check_knowledge_records(tmp_path, repo_id="main")
    assert [item["id"] for item in data["records"]] == [record["id"]]
    assert len(problems) == 1
    assert problems[0].code == "knowledge_record_unreadable"
    assert problems[0].path == "docs/knowledge/records/broken.json"
