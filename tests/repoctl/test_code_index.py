from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.repository.test_repositories import init_repo


def test_index_code_extracts_python_facts(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    rel = "backend/auth/token_service.py"
    (repo / "backend/auth").mkdir(parents=True)
    (repo / rel).write_text(
        "import hashlib\n"
        "from pathlib import Path\n\n"
        "class TokenService:\n"
        "    def issue(self, value):\n"
        "        Path('token.txt').write_text(hashlib.sha256(value).hexdigest())\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["index", "code", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "index.code"
    entry = next(file for file in payload["data"]["files"] if file["path"] == rel)
    assert entry["language"] == "python"
    assert entry["symbols"] == ["TokenService", "issue"]
    assert entry["imports"] == ["hashlib", "pathlib.Path"]
    assert entry["import_occurrences"] == [
        {"form": "module", "imported_name": "", "level": 0, "module": "hashlib", "raw_import": "hashlib"},
        {"form": "from", "imported_name": "Path", "level": 0, "module": "pathlib", "raw_import": "pathlib.Path"},
    ]
    assert entry["module_certain_bindings"] == ["Path", "TokenService", "hashlib"]
    assert "hashlib.sha256" in entry["calls"]


def test_index_code_keeps_typescript_inventory_separate_from_semantic_provider(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    rel = "frontend/src/api/billingGateway.ts"
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / rel).write_text(
        "import axios from 'axios';\n"
        "export class BillingClient {}\n"
        "export const charge = () => fetch('/charge');\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["index", "code", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    entry = next(file for file in payload["data"]["files"] if file["path"] == rel)
    assert entry["language"] == "typescript"
    assert entry["symbols"] == []
    assert entry["imports"] == []
    assert entry["calls"] == []


def test_index_code_extracts_dart_and_csharp_inventory(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    (repo / "lib").mkdir()
    (repo / "Assets/Scripts").mkdir(parents=True)
    (repo / "lib/main.dart").write_text("import 'package:demo/app.dart';\nvoid main() {}\n", encoding="utf-8")
    (repo / "Assets/Scripts/PlayerController.cs").write_text("namespace Game { public class PlayerController {} }\n", encoding="utf-8")
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["index", "code", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    dart_entry = next(file for file in payload["data"]["files"] if file["path"] == "lib/main.dart")
    csharp_entry = next(file for file in payload["data"]["files"] if file["path"] == "Assets/Scripts/PlayerController.cs")
    assert dart_entry["language"] == "dart"
    assert dart_entry["imports"] == ["package:demo/app.dart"]
    assert dart_entry["parse_status"] == "ok"
    assert csharp_entry["language"] == "csharp"
    assert csharp_entry["symbols"] == []
    assert csharp_entry["parse_status"] == "skipped"
    assert payload["data"]["summary"]["languages"]["dart"] == 1
    assert payload["data"]["summary"]["languages"]["csharp"] == 1


def test_index_code_changed_requires_repo_git(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["index", "code", "--changed", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(problem["code"] == "repository_not_found" for problem in payload["problems"])


def test_index_code_reports_truncation_separately_from_total(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    for index in range(3):
        (repo / f"mod{index}.py").write_text(f"def run_{index}():\n    return {index}\n", encoding="utf-8")
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(tmp_path))

    assert main(["index", "code", "--limit", "2", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    summary = payload["data"]["summary"]
    assert summary["total"] == 3
    assert summary["returned"] == 2
    assert summary["truncated"] is True
    assert summary["dropped_count"] == 1
