from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.repoctl.knowledge_records import (
    add_knowledge_record,
    query_knowledge_records,
    knowledge_status,
    show_knowledge_record,
    check_knowledge_records,
)


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


def test_claim_text_and_source_bytes_are_preserved(
    tmp_path: Path,
) -> None:
    (tmp_path / "repos").mkdir()
    content = b"value = 42\r\n"
    (tmp_path / "repos/source.py").write_bytes(content)
    claim = "같은 결제 요청은 저장된 결과를 반환한다. 재시도는 추가 결제를 생성하지 않는다."
    record = add(tmp_path, claim)
    assert record["claim"] == claim
    assert (
        record["source_refs"][0]["content_sha256"]
        == "sha256:" + hashlib.sha256(content).hexdigest()
    )
    data, problems, warnings = query_knowledge_records(
        tmp_path, repo_id="main", query="결제 요청"
    )
    assert not problems and not warnings
    assert data["results"][0]["record"]["source_statuses"][0]["status"] == "unchanged"


def test_query_observes_only_selected_sources_and_status_reads_records(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "repos").mkdir()
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
    assert data["result_count"] == 1 and data["matched_count"] == 1
    assert data["results"][0]["exact_match"] is True
    assert data["results"][0]["matched_terms"] == ["decision1"]
    assert observed == ["source1.py"]

    counts, problems, warnings = query_knowledge_records(
        tmp_path, repo_id="main", query="decision", limit=0
    )
    assert not problems and not warnings
    assert counts["matched_count"] == 3 and counts["result_count"] == 0
    assert counts["results"] == []
    assert observed == ["source1.py"]

    all_matches, problems, warnings = query_knowledge_records(
        tmp_path, repo_id="main", query="decision", limit=-1
    )
    assert not problems and not warnings
    assert all_matches["matched_count"] == all_matches["result_count"] == 3
    assert all_matches["truncated"] is False
    assert len(observed[1:]) == 3
    assert set(observed[1:]) == {"source0.py", "source1.py", "source2.py"}


def test_missing_source_is_reported_without_automatic_relocation(
    tmp_path: Path,
) -> None:
    (tmp_path / "repos").mkdir()
    original = tmp_path / "repos/source.py"
    original.write_text("value = 42\n")
    record = add(tmp_path, "Stable source location")
    original.rename(tmp_path / "repos/moved.py")
    data, problems = show_knowledge_record(
        tmp_path, repo_id="main", record_id=record["id"]
    )
    assert not problems
    resolution = data["record"]["source_statuses"][0]
    assert resolution["status"] == "missing"
    assert resolution["path"] == "repos/source.py"


def test_replacement_excludes_superseded_record_from_default_query(
    tmp_path: Path,
) -> None:
    (tmp_path / "repos").mkdir()
    (tmp_path / "repos/source.py").write_text("value = 42\n")
    first = add(tmp_path, "original cache decision")
    second = add(tmp_path, "replacement cache decision", replaces=[first["id"]])
    data, _, _ = query_knowledge_records(tmp_path, repo_id="main", query="cache")
    assert [item["record"]["id"] for item in data["results"]] == [second["id"]]
    history, _, _ = query_knowledge_records(
        tmp_path, repo_id="main", query="cache", include_superseded=True
    )
    assert {item["record"]["status"] for item in history["results"]} == {
        "active",
        "superseded",
    }


def test_unrelated_malformed_record_does_not_hide_or_block_selected_records(
    tmp_path: Path,
) -> None:
    (tmp_path / "repos").mkdir()
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
    replacement = add(tmp_path, "replacement decision", replaces=[record["id"]])
    assert replacement["replaces"] == [record["id"]]


def test_claim_only_record_and_free_form_kind(tmp_path: Path) -> None:
    data, problems, warnings = add_knowledge_record(
        tmp_path, repo_id="", claim="Scheduled cleanup retains failed requests for investigation.",
    )
    assert not problems and not warnings
    record = data["record"]
    assert not set(record) & {"kind", "reason", "source_refs", "applies_to", "replaces"}
    shown, problems = show_knowledge_record(tmp_path, repo_id="", record_id=record["id"])
    assert not problems
    assert shown["record"]["status"] == "active"
    assert shown["record"]["source_statuses"] == []
    labeled, problems, _ = add_knowledge_record(
        tmp_path, repo_id="unavailable-checkout", claim="Cleanup runs after the transaction commits.",
        kind="operating note", applies_to=["./src/cleanup.py"],
    )
    assert not problems and labeled["record"]["kind"] == "operating note"
    assert labeled["record"]["repo_id"] == "unavailable-checkout"
    assert labeled["record"]["applies_to"] == {"paths": ["src/cleanup.py"]}


def test_source_observation_does_not_change_record_lifecycle(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "repos").mkdir()
    path = tmp_path / "repos/source.py"
    path.write_bytes(b"value = 1\r\n")
    record = add(tmp_path, "The configured value starts at one.")
    path.write_bytes(b"value = 2\r\n")
    data, problems = check_knowledge_records(tmp_path, repo_id="main")
    assert data["records"][0]["id"] == record["id"]
    assert data["records"][0]["status"] == "active"
    source = data["records"][0]["source_statuses"][0]
    assert source["status"] == "changed"
    assert source["recorded_sha256"] == record["source_refs"][0]["content_sha256"]
    assert source["current_sha256"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    assert problems[0].code == "knowledge_source_changed"

    read_bytes = Path.read_bytes

    def unreadable_source(candidate: Path) -> bytes:
        if candidate == path:
            raise PermissionError("source read denied")
        return read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", unreadable_source)
    data, problems = check_knowledge_records(tmp_path, repo_id="main")
    assert data["records"][0]["source_statuses"][0]["status"] == "unavailable"
    assert problems[0].code == "knowledge_source_unavailable"


def test_explicit_task_citation_does_not_infer_applicability(tmp_path: Path) -> None:
    relative = "docs/archive/tasks/completed.md"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    content = b"---\nid: request-retries\nstatus: done\n---\n\nRetries reuse the stored result.\n"
    path.write_bytes(content)
    data, problems, warnings = add_knowledge_record(
        tmp_path, repo_id="", claim="Retrying a request returns its stored result.",
        sources=[relative],
    )
    assert not problems and not warnings
    assert data["record"]["source_refs"] == [{
        "path": relative,
        "content_sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
    }]
    assert data["record"].get("applies_to", {}).get("paths", []) == []
    assert path.read_bytes() == content


def test_source_outside_workspace_is_not_read(tmp_path: Path) -> None:
    data, problems, warnings = add_knowledge_record(
        tmp_path, repo_id="", claim="Request retries share the original result.",
        sources=["../external.md"],
    )
    assert not data and not warnings
    assert problems[0].code == "knowledge_source_unavailable"
    assert not (tmp_path / "docs/knowledge/records").exists()
