from __future__ import annotations

import json
from pathlib import Path

from tests.repoctl.task_lifecycle_helpers import write_workspace
from tools.repoctl.cli import main
from tools.repoctl.template_contract import TEMPLATE_CONTRACT_SCHEMA


def test_unversioned_legacy_and_supported_v1_resume_are_readable(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "resume", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["selection"]["status"] == "no_live"

    marker = tmp_path / "docs/template-contract.json"
    marker.write_text(json.dumps({"schema": TEMPLATE_CONTRACT_SCHEMA, "version": "1.3.2"}) + "\n", encoding="utf-8")
    before = marker.read_bytes()
    assert main(["task", "resume", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["selection"]["status"] == "no_live"
    assert marker.read_bytes() == before


def test_incompatible_or_corrupt_template_version_fails_closed(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    marker = tmp_path / "docs/template-contract.json"
    for version, code in [("2.0.0", "template_contract_unsupported"), ("not-a-version", "template_contract_invalid")]:
        marker.write_text(json.dumps({"schema": TEMPLATE_CONTRACT_SCHEMA, "version": version}) + "\n", encoding="utf-8")
        original = marker.read_bytes()
        assert main(["task", "resume", "--json"]) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["problems"][0]["code"] == code
        assert marker.read_bytes() == original

    marker.unlink()
    target = tmp_path / "docs/misleading-contract.json"
    target.write_text(json.dumps({"schema": TEMPLATE_CONTRACT_SCHEMA, "version": "1.0.0"}), encoding="utf-8")
    marker.symlink_to(target.name)
    assert main(["task", "resume", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == "template_contract_invalid"


def test_unsupported_major_blocks_document_lifecycle_before_any_write(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    # In real adopters debug_mode can be enabled and used to write event logs.
    # The unsupported-major diagnostic must run before even that side effect.
    settings = tmp_path / "docs/repoctl.json"
    config = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    config["debug_mode"] = True
    settings.write_text(json.dumps(config) + "\n", encoding="utf-8")
    marker = tmp_path / "docs/template-contract.json"
    marker.write_text(
        json.dumps({"schema": TEMPLATE_CONTRACT_SCHEMA, "version": "2.0.0"}) + "\n",
        encoding="utf-8",
    )

    def protected_snapshot() -> dict[str, bytes]:
        paths = [tmp_path / "docs/BOARD.md"]
        for relative in ("docs/tasks", "docs/archive/tasks"):
            directory = tmp_path / relative
            if directory.exists():
                paths.extend(path for path in directory.rglob("*") if path.is_file())
        return {str(path.relative_to(tmp_path)): path.read_bytes()
                for path in paths if path.is_file()}

    before = protected_snapshot()
    task_id = "T-20260609184046Z"
    commands = [
        ["task", "create", "Unsafe change", "--slug", "unsafe-change", "--json"],
        ["task", "list", "--json"],
        ["task", "show", task_id, "--json"],
        ["task", "start", task_id, "--json"],
        ["task", "resume", "--json"],
        ["task", "discovery", "add", task_id, "--chosen", "docs/BOARD.md", "--json"],
        ["task", "handoff", "bind", task_id, "--json"],
        ["task", "doctor", task_id, "--json"],
        ["task", "finish", task_id, "--json"],
        ["task", "block", task_id, "--reason", "Unsafe", "--json"],
        ["task", "cancel", task_id, "--reason", "Unsafe", "--json"],
        ["task", "log", "append", task_id, "Unsafe log", "--json"],
        ["task", "migrate-slop", "--apply", "--json"],
        ["backlog", "list", "--json"],
        ["check", "--json"],
        ["repo", "check", "--json"],
        ["upgrade", "postflight", "--json"],
        ["upgrade", "status", "--json"],
        ["knowledge", "status", "--workspace", "--json"],
        ["graph", "build", "--workspace", "--json"],
    ]
    for command in commands:
        assert main(command) == 2, command
        payload = json.loads(capsys.readouterr().out)
        assert payload["problems"][0]["code"] == "template_contract_unsupported", command
        assert protected_snapshot() == before, command


def test_explicit_upgrade_target_contract_is_checked_before_debug_write(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    origin = tmp_path / "origin"
    target = tmp_path / "target"
    origin.mkdir()
    target.mkdir()
    write_workspace(origin)
    write_workspace(target)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: origin)
    settings = origin / "docs/repoctl.json"
    config = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    config["debug_mode"] = True
    settings.write_text(json.dumps(config) + "\n", encoding="utf-8")
    marker = target / "docs/template-contract.json"
    marker.write_text(json.dumps({"schema": TEMPLATE_CONTRACT_SCHEMA, "version": "2.0.0"}) + "\n", encoding="utf-8")
    before_origin = {p.relative_to(origin).as_posix(): p.read_bytes()
                     for p in (origin / "docs/tasks").rglob("*") if p.is_file()}
    before_target = {p.relative_to(target).as_posix(): p.read_bytes()
                     for p in (target / "docs/tasks").rglob("*") if p.is_file()}
    assert main(["upgrade", "postflight", "--workspace-root", str(target), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == "template_contract_unsupported"
    assert {p.relative_to(origin).as_posix(): p.read_bytes()
            for p in (origin / "docs/tasks").rglob("*") if p.is_file()} == before_origin
    assert {p.relative_to(target).as_posix(): p.read_bytes()
            for p in (target / "docs/tasks").rglob("*") if p.is_file()} == before_target
