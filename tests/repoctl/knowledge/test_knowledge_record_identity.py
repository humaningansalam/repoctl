from __future__ import annotations

import json
from pathlib import Path

from tests.repoctl.knowledge_test_helpers import _add_knowledge_source, _setup_knowledge_workspace
from tools.repoctl.cli import main
from tools.repoctl.knowledge_projection import knowledge_projection_path, read_knowledge_record_sources


def _payload(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_renamed_v2_file_is_rejected_by_query_check_show_and_rebuild(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    original = _add_knowledge_source(capsys, claim="Record identity requires its filename.")["data"]["record"]
    record_id = original["id"]
    directory = tmp_path / "docs/knowledge/records"
    (directory / f"{record_id}.json").rename(directory / "K-20260101010101Z--renamed.json")

    commands = [
        ["knowledge", "query", "record identity filename", "--repo-id", "main", "--json"],
        ["knowledge", "check", "--repo-id", "main", "--json"],
        ["knowledge", "show", record_id, "--repo-id", "main", "--json"],
        ["knowledge", "rebuild", "--repo-id", "main", "--json"],
    ]
    for command in commands:
        assert main(command) == 1, command
        result = _payload(capsys)
        assert "knowledge_record_id_mismatch" in {
            problem["code"] for problem in result["problems"]
        }, command


def test_valid_v2_file_still_supports_degraded_direct_query(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    original = _add_knowledge_source(capsys, claim="Valid direct records remain usable.")["data"]["record"]
    knowledge_projection_path(tmp_path, repo_id="main").unlink()

    assert main(["knowledge", "query", "valid direct records", "--repo-id", "main", "--json"]) == 0
    result = _payload(capsys)
    assert [row["record"]["id"] for row in result["data"]["results"]] == [original["id"]]
    assert result["data"]["lifecycle"]["projection_status"] == "degraded_direct"


def test_direct_sources_reject_duplicate_identity_and_symlink(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    original = _add_knowledge_source(capsys, claim="File-backed identity is single-source.")["data"]["record"]
    directory = tmp_path / "docs/knowledge/records"
    canonical = directory / f"{original['id']}.json"
    alias = directory / "K-20260101010101Z--duplicate.json"
    alias.write_bytes(canonical.read_bytes())
    sources, problems = read_knowledge_record_sources(tmp_path)
    assert len(sources) == 2
    assert {"knowledge_record_id_mismatch", "knowledge_record_duplicate_id"} <= {
        item.code for item in problems
    }

    alias.unlink()
    alias.symlink_to(canonical.name)
    sources, problems = read_knowledge_record_sources(tmp_path)
    assert len(sources) == 1
    assert any(item.code == "knowledge_record_symlink_invalid" for item in problems)
