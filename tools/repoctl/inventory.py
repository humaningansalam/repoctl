from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .git import repo_changed_entries, repo_git_state
from .io import Problem
from .language_profiles import default_indexing_excludes
from .repositories import RepoTarget, default_repo_target


@dataclass(frozen=True)
class InventoryFile:
    path: str
    workspace_path: str
    classification: str = "indexed_only"
    change: str = ""
    old_path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "workspace_path": self.workspace_path,
            "classification": self.classification,
            "change": self.change,
            "old_path": self.old_path,
        }


def file_inventory(
    root: Path, *, changed: bool = False, target: RepoTarget | None = None
) -> tuple[list[InventoryFile], list[Problem], dict[str, Any]]:
    selected = target or default_repo_target(root)
    state = repo_git_state(root, selected)
    data: dict[str, Any] = {
        "repository": selected.to_dict() if selected else {},
        "scope": "changed" if changed else "all",
    }
    if not state.available or selected is None:
        return (
            [],
            [
                Problem(
                    "error", "repository_git_unavailable", state.reason, state.repo_path
                )
            ],
            data,
        )
    changes: dict[str, tuple[str, str]] = {}
    if changed:
        entries, state = repo_changed_entries(root, selected)
        if not state.available:
            return (
                [],
                [
                    Problem(
                        "error",
                        "repository_git_unavailable",
                        state.reason,
                        state.repo_path,
                    )
                ],
                data,
            )
        changes = {path: (kind, old_path) for kind, path, old_path in entries}
        paths = sorted(changes)
    else:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=selected.root_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode:
            return (
                [],
                [
                    Problem(
                        "error",
                        "inventory_unavailable",
                        result.stderr.decode("utf-8", errors="replace").strip(),
                        selected.display_path,
                    )
                ],
                data,
            )
        paths = sorted({os.fsdecode(raw) for raw in result.stdout.split(b"\0") if raw})
    excludes = default_indexing_excludes()
    files: list[InventoryFile] = []
    for path in paths:
        if any(fnmatch(path, pattern) for pattern in excludes):
            continue
        source = selected.root_path / path
        try:
            source.resolve().relative_to(selected.root_path.resolve())
            outside = False
        except (OSError, ValueError, RuntimeError):
            outside = True
        excluded = outside or source.is_symlink()
        kind, old_path = changes.get(path, ("", ""))
        workspace_path = (Path(selected.display_path) / path).as_posix()
        files.append(
            InventoryFile(
                path,
                workspace_path,
                "excluded" if excluded else "indexed_only",
                kind,
                old_path,
            )
        )
    data["summary"] = {
        "total": len(files),
        "indexed_only": sum(file.classification == "indexed_only" for file in files),
        "excluded": sum(file.classification == "excluded" for file in files),
    }
    return files, [], data
