from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tools.repoctl.release import build_release_archive
from tools.repoctl.io import RepoctlError
from tools.repoctl.upgrade import plan_upgrade


def test_build_release_archive_uses_manifest_managed_paths(tmp_path: Path) -> None:
    root = tmp_path / "source"
    out = tmp_path / "dist"
    manifest = {
        "schema_version": 1,
        "package": "agent-workspace-control-plane",
        "version": "0.1.0",
        "replace_paths": ["scripts/repoctl"],
        "create_paths": ["docs/workflows/repo-metadata.md"],
        "preserve_paths": ["repos/**", "docs/BOARD.md"],
    }
    prefix = f"{manifest['package']}-{manifest['version']}"
    (root / "scripts").mkdir(parents=True)
    (root / "docs/workflows").mkdir(parents=True)
    (root / "scripts/repoctl").write_text("tool\n", encoding="utf-8")
    (root / "docs/workflows/repo-metadata.md").write_text("workflow\n", encoding="utf-8")
    (root / "repoctl-upgrade-manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    archive_path = build_release_archive(root, out)

    assert archive_path.name == f"{prefix}.tar.gz"
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    assert f"{prefix}/repoctl-upgrade-manifest.json" in names
    assert f"{prefix}/scripts/repoctl" in names
    assert f"{prefix}/docs/workflows/repo-metadata.md" in names


def test_release_archive_modes_are_independent_of_checkout_umask(tmp_path: Path) -> None:
    root = tmp_path / "source"
    normal = root / "docs/PRD.md"
    executable = root / "scripts/repoctl"
    normal.parent.mkdir(parents=True)
    executable.parent.mkdir(parents=True)
    normal.write_text("# Current product\n", encoding="utf-8")
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (root / "repoctl-upgrade-manifest.json").write_text(
        json.dumps({
            "schema_version": 1,
            "package": "agent-workspace-control-plane",
            "version": "0.1.0",
            "replace_paths": ["docs/PRD.md", "scripts/repoctl"],
            "create_paths": [],
            "preserve_paths": [],
        }),
        encoding="utf-8",
    )

    normal.chmod(0o644)
    executable.chmod(0o755)
    first = build_release_archive(root, tmp_path / "first")
    normal.chmod(0o664)
    executable.chmod(0o775)
    second = build_release_archive(root, tmp_path / "second")

    assert first.read_bytes() == second.read_bytes()
    prefix = "agent-workspace-control-plane-0.1.0"
    with tarfile.open(second, "r:gz") as archive:
        assert archive.getmember(f"{prefix}/docs/PRD.md").mode == 0o644
        assert archive.getmember(f"{prefix}/scripts/repoctl").mode == 0o755


def test_release_rejects_unsafe_identity_and_preserves_existing_artifact_on_failure(tmp_path: Path) -> None:
    root = tmp_path / "source"
    out = tmp_path / "dist"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/repoctl").write_text("tool\n", encoding="utf-8")
    manifest_path = root / "repoctl-upgrade-manifest.json"
    manifest = {
        "schema_version": 1,
        "package": "../escaped",
        "version": "0.1.0",
        "replace_paths": ["scripts/repoctl"],
        "create_paths": [],
        "preserve_paths": [],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SystemExit, match="safe archive component"):
        build_release_archive(root, out)

    assert not out.exists()
    manifest["package"] = "agent-workspace-control-plane"
    manifest["replace_paths"].append("missing.py")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    out.mkdir()
    existing = out / "agent-workspace-control-plane-0.1.0.tar.gz"
    existing.write_bytes(b"known-good")

    with pytest.raises(SystemExit, match="missing or not a regular file"):
        build_release_archive(root, out)

    assert existing.read_bytes() == b"known-good"
    assert not list(out.glob("*.tmp"))


def test_release_manifest_manages_every_public_runtime_module() -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    managed = {*manifest["replace_paths"], *manifest["create_paths"]}
    runtime_modules = {
        path.relative_to(source_root).as_posix()
        for path in (source_root / "tools/repoctl").rglob("*.py")
    }
    assert runtime_modules
    assert runtime_modules <= managed
    assert "contracts/template-document-v1.json" in managed
    assert "scripts/repoctl" in managed

def test_release_archive_excludes_dev_tests_and_template_scaffold(tmp_path: Path) -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    prefix = f"{manifest['package']}-{manifest['version']}/"
    with tarfile.open(archive_path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
    relative_names = {member.name.removeprefix(prefix) for member in members}
    assert any((source_root / "tests/repoctl").rglob("*.py"))
    assert not any(name.startswith("tests/") for name in relative_names)
    assert "fixtures/conformance/cases.json" in relative_names
    assert not any(name.startswith("docs/tasks/") for name in relative_names)
    assert "AGENTS.md" not in relative_names
    assert "docs/BOARD.md" not in relative_names

def test_release_archive_runs_public_commands_in_external_workspace(tmp_path: Path) -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    extract_dir = tmp_path / "extract-current"
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(extract_dir, filter="data")
    package_root = extract_dir / f"{manifest['package']}-{manifest['version']}"
    workspace = tmp_path / "template"
    fixture = source_root / "tests/fixtures/template-contract-1.0.0"
    shutil.copytree(fixture, workspace)
    (workspace / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n## Backlog\n", encoding="utf-8")
    assert (package_root / "tools/repoctl/repositories.py").is_file()
    assert (package_root / "tools/repoctl/graph_workflow.py").is_file()
    assert not (package_root / "docs/archive/tasks").exists()
    assert not (package_root / "docs/knowledge/records").exists()
    env = {**os.environ, "REPOCTL_WORKSPACE_ROOT": str(workspace)}
    env.pop("PYTHON", None)
    repository_list = subprocess.run(
        [str(package_root / "scripts/repoctl"), "repo", "list", "--json"],
        cwd=workspace, env=env, text=True, capture_output=True, check=False,
    )
    assert repository_list.returncode == 0, repository_list.stderr
    assert json.loads(repository_list.stdout)["command"] == "repo.list"
    checks = [
        (["graph", "search", "--help"], "query"),
        (["graph", "--help"], "query"),
        (["knowledge", "--help"], "add"),
        (["knowledge", "add", "--help"], "--reason"),
    ]
    for arguments, expected in checks:
        result = subprocess.run(
            [str(package_root / "scripts/repoctl"), *arguments],
            cwd=workspace, env=env, text=True, capture_output=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout

def test_release_workflow_gates_publish_on_full_regression_and_public_identity() -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    workflow = (source_root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    ci = (source_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert re.findall(r"uses:\s+[^@\s]+@v\d+", workflow) == []
    assert "needs: verify" in workflow
    assert "pytest -q" in workflow
    assert "uv sync --locked --extra structural" in workflow
    assert "pytest -q" in ci
    assert "Verify release tag" in workflow
    assert "gh release create" in workflow
    assert "gh release download" in workflow
    assert "public-$ASSET" in workflow

def test_release_help_is_read_only_and_archives_are_reproducible(tmp_path: Path) -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    result = subprocess.run(
        [sys.executable, "-m", "tools.repoctl.release", "--help"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": source_root.as_posix()},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert not (tmp_path / "--help").exists()
    first = build_release_archive(source_root, tmp_path / "first")
    second = build_release_archive(source_root, tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()


def test_current_archive_cannot_embed_runtime_into_split_template(tmp_path: Path) -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    release_root = tmp_path / "release"
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(release_root, filter="data")
    runtime = next(release_root.iterdir())
    workspace = tmp_path / "template"
    shutil.copytree(source_root / "tests/fixtures/template-contract-1.0.0", workspace)
    (workspace / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n## Backlog\n", encoding="utf-8")
    (workspace / "scripts").mkdir()
    launcher = workspace / "scripts/repoctl"
    launcher.write_text(
        '#!/usr/bin/env bash\nREPOCTL_HOME=/external/repoctl\n'
        'exec env REPOCTL_WORKSPACE_ROOT="$PWD" "$REPOCTL_HOME/scripts/repoctl" "$@"\n',
        encoding="utf-8",
    )
    protected = {path.relative_to(workspace).as_posix(): path.read_bytes()
                 for path in workspace.rglob("*") if path.is_file()}
    with pytest.raises(RepoctlError) as error:
        plan_upgrade(workspace, source=runtime)
    assert error.value.code == "upgrade_split_workspace_forbidden"
    assert {path.relative_to(workspace).as_posix(): path.read_bytes()
            for path in workspace.rglob("*") if path.is_file()} == protected
    assert not (workspace / "tools/repoctl").exists()

def test_runtime_checkout_ignores_local_build_artifacts_not_adopter_records() -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    ignored = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=source_root,
        input=".venv/bin/python\n.pytest_cache/v/cache/nodeids\ndist/repoctl.tar.gz\n",
        text=True, capture_output=True, check=False,
    )
    assert ignored.returncode == 0
    assert len(ignored.stdout.splitlines()) == 3
    records = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=source_root,
        input="docs/knowledge/records/K-example.json\ndocs/knowledge/events/E-example.json\n",
        text=True, capture_output=True, check=False,
    )
    assert records.returncode == 1, records.stdout

def test_current_manifest_is_runtime_owned_and_does_not_claim_adopter_docs() -> None:
    root = next(parent for parent in Path(__file__).resolve().parents if (parent / "repoctl-upgrade-manifest.json").is_file())
    manifest = json.loads((root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    managed = {*manifest["replace_paths"], *manifest["create_paths"]}
    for path in ("AGENTS.md", "docs/README.md", "docs/tasks/TEMPLATE.md",
                 "docs/tasks/PARENT_TEMPLATE.md", "docs/BOARD.md", "docs/PRD.md",
                 "docs/knowledge/records/K-example.json", ".gitignore"):
        assert path not in managed, path
    assert "README.md" in manifest["create_paths"]
    assert "README.md" not in manifest["replace_paths"]
    assert "tools/repoctl/knowledge_migration.py" in managed
    assert "tools/repoctl/graph_tree_sitter_provider.py" in managed
    assert "tools/repoctl/task_context_docs.py" in managed
