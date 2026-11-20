from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from tools.repoctl.repositories import RepoTarget
from tools.repoctl.source_index import materialize_source_index, source_index_path
from tools.repoctl.source_search import search_sources


def _repository(root: Path) -> RepoTarget:
    repo = root / "repos"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    return RepoTarget("main", repo, "repos", "reserved")


def _write(root: Path, path: str, text: str) -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def test_search_reads_actual_sources_and_keeps_records_separate(tmp_path: Path) -> None:
    target = _repository(tmp_path)
    _write(tmp_path, "repos/src/tasks.py", "def scheduleTask():\n    return 'needle'\n")
    _write(tmp_path, "repos/data/settings.json", '{"needle": true}\n')
    _write(tmp_path, "docs/design.md", "# Needle\nAn authored design.\n")
    _write(tmp_path, "repos/docs/tasks/scheduler.md", "# Needle scheduling\nProduct design documentation.\n")
    for path in ("docs/tasks/TASK-1.md", "docs/archive/tasks/TASK-2.md", "docs/knowledge/records/K-1.json", "docs/BOARD.md"):
        _write(tmp_path, path, "needle " * 100)
    data, problems = search_sources(tmp_path, target=target, query="needle", limit=-1)
    assert problems == []
    assert {item["path"] for item in data["results"]} == {
        "repos/src/tasks.py", "repos/data/settings.json", "docs/design.md", "repos/docs/tasks/scheduler.md",
    }
    assert data["coverage"]["status"] == "complete"
    assert not (tmp_path / ".repoctl-state").exists()
    limited, problems = search_sources(tmp_path, target=target, query="needle", limit=1)
    assert problems == []
    assert limited["match_count"] == 4
    assert limited["returned_count"] == 1
    assert limited["truncated"] is True


def test_indexed_search_and_live_scan_have_identical_results(tmp_path: Path) -> None:
    target = _repository(tmp_path)
    _write(tmp_path, "repos/src/auth.py", "def verifyToken(token):\n    return token == 'accepted'\n")
    _write(tmp_path, "repos/tests/test_auth.py", "from src.auth import verifyToken\nassert verifyToken('accepted')\n")
    _write(tmp_path, "docs/auth.md", "Token authentication uses a verifier.\n")
    live, problems = search_sources(tmp_path, target=target, query="verify token", limit=-1)
    assert problems == []
    metadata, problems = materialize_source_index(tmp_path, target=target)
    assert problems == []
    assert metadata["files_indexed"] == 3
    indexed, problems = search_sources(tmp_path, target=target, query="verify token", limit=-1)
    assert problems == []
    assert indexed["results"] == live["results"]
    assert indexed["index"]["status"] == "current"
    reused, problems = materialize_source_index(tmp_path, target=target)
    assert problems == []
    assert reused["updated_paths"] == []


def test_search_overlays_changed_added_and_deleted_files_without_writing(tmp_path: Path, monkeypatch) -> None:
    target = _repository(tmp_path)
    _write(tmp_path, "repos/changed.py", "needle = 'old'\n")
    _write(tmp_path, "repos/deleted.py", "needle = 'deleted'\n")
    _write(tmp_path, "repos/stable.py", "needle = 'stable'\n")
    subprocess.run(["git", "add", "."], cwd=target.root_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "--quiet", "-m", "source fixture"],
        cwd=target.root_path, check=True,
    )
    materialize_source_index(tmp_path, target=target)
    database = source_index_path(tmp_path, target)
    before = database.read_bytes()
    timestamp = database.stat().st_mtime_ns
    _write(tmp_path, "repos/changed.py", "needle = 'new'\n")
    _write(tmp_path, "repos/added.py", "needle = 'added'\n")
    (tmp_path / "repos/deleted.py").unlink()
    overlaid, problems = search_sources(tmp_path, target=target, query="needle", limit=-1)
    assert problems == []
    assert overlaid["index"]["status"] == "overlay"
    assert overlaid["index"]["deleted_files"] == 1
    assert {item["path"] for item in overlaid["results"]} == {
        "repos/changed.py", "repos/stable.py", "repos/added.py",
    }
    assert "'new'" in next(item["snippet"] for item in overlaid["results"] if item["path"].endswith("changed.py"))
    with monkeypatch.context() as patch:
        patch.setattr("tools.repoctl.source_search.open_source_index", lambda *args, **kwargs: (None, None))
        live, problems = search_sources(tmp_path, target=target, query="needle", limit=-1)
    assert problems == []
    assert live["results"] == overlaid["results"]
    assert database.read_bytes() == before
    assert database.stat().st_mtime_ns == timestamp


def test_search_preserves_byte_identity_unicode_and_exact_path(tmp_path: Path) -> None:
    target = _repository(tmp_path)
    path = tmp_path / "repos/인증.py"
    raw = "# 인증 처리\r\ndef verifyToken():\r\n    return True\r\n".encode("utf-8")
    path.write_bytes(raw)
    _write(tmp_path, "repos/notes.txt", "인증 " * 30)
    data, problems = search_sources(tmp_path, target=target, query="인증.py")
    assert problems == []
    first = data["results"][0]
    assert first["path"] == "repos/인증.py"
    assert first["match_kind"] == "exact_path"
    assert first["content_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    unicode_result, problems = search_sources(tmp_path, target=target, query="인증")
    assert problems == []
    assert {item["path"] for item in unicode_result["results"]} == {"repos/인증.py", "repos/notes.txt"}


def test_unreadable_source_reports_partial_coverage(tmp_path: Path, monkeypatch) -> None:
    target = _repository(tmp_path)
    _write(tmp_path, "repos/readable.py", "needle = True\n")
    blocked = tmp_path / "repos/blocked.py"
    blocked.write_text("needle = False\n")
    read_bytes = Path.read_bytes
    def read(path: Path) -> bytes:
        if path == blocked:
            raise PermissionError("fixture denied")
        return read_bytes(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    data, problems = search_sources(tmp_path, target=target, query="needle")
    assert data["coverage"]["status"] == "partial"
    assert data["coverage"]["files_searched"] == 1
    assert [problem.code for problem in problems] == ["source_unreadable"]
    assert data["results"][0]["path"] == "repos/readable.py"


def test_corrupt_index_falls_back_without_rewriting_it(tmp_path: Path) -> None:
    target = _repository(tmp_path)
    _write(tmp_path, "repos/app.py", "needle = True\n")
    database = source_index_path(tmp_path, target)
    database.parent.mkdir(parents=True)
    database.write_bytes(b"invalid sqlite")
    data, problems = search_sources(tmp_path, target=target, query="needle")
    assert data["index"]["status"] == "live_scan"
    assert data["results"][0]["path"] == "repos/app.py"
    assert [problem.code for problem in problems] == ["source_index_unavailable"]
    assert database.read_bytes() == b"invalid sqlite"
