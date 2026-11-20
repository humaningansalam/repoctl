from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeAlias

from .repositories import RepoTarget, default_repo_target
from .io import RepoctlError


ChangedEntry: TypeAlias = tuple[str, str, str]


@dataclass(frozen=True)
class RepoGitState:
    available: bool
    reason: str = ""
    repo_id: str = ""
    repo_path: str = ""
    problem_code: str = ""


class GitNameStatusProblem(StrEnum):
    MALFORMED = "git_name_status_malformed"
    INVALID_PATH = "git_name_status_invalid_path"
    UNMERGED = "unmerged_path"
    UNSUPPORTED = "git_change_type_unsupported"


class GitObservationProblem(StrEnum):
    COMMAND_FAILED = "git_observation_command_failed"
    INDEX_MALFORMED = "git_index_observation_malformed"
    FILE_UNREADABLE = "repository_file_unreadable"
    FILE_CHANGED = "repository_file_changed_during_read"


@dataclass(frozen=True)
class GitNameStatusResult:
    entries: tuple[ChangedEntry, ...]
    problem: GitNameStatusProblem | None = None
    path: str = ""


@dataclass(frozen=True)
class _GitBytesResult:
    data: bytes
    problem: GitObservationProblem | None = None


def _target(root: Path, target: RepoTarget | None = None) -> RepoTarget | None:
    return target or default_repo_target(root)


def repo_git_state(root: Path, target: RepoTarget | None = None) -> RepoGitState:
    try:
        selected = _target(root, target)
    except (OSError, RepoctlError) as exc:
        return RepoGitState(False, str(exc))
    if selected is None:
        return RepoGitState(False, "product repository directory is missing")
    repo = selected.root_path
    if not (repo / ".git").exists():
        return RepoGitState(False, f"{selected.display_path}/ is not an independent git repository", selected.id, selected.display_path)
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    except OSError as exc:
        return RepoGitState(False, str(exc), selected.id, selected.display_path, GitObservationProblem.COMMAND_FAILED.value)
    if result.returncode != 0:
        return RepoGitState(False, f"{selected.display_path}/ git metadata is not usable", selected.id, selected.display_path)
    try:
        top = Path(result.stdout.strip()).resolve()
        if top != repo.resolve():
            return RepoGitState(False, f"{selected.display_path}/ git resolves outside repository root", selected.id, selected.display_path)
    except OSError:
        return RepoGitState(False, f"{selected.display_path}/ git root cannot be resolved", selected.id, selected.display_path)
    return RepoGitState(True, repo_id=selected.id, repo_path=selected.display_path)


def normalize_repo_path(path: str | Path) -> str:
    raw = str(path)
    if Path(raw).is_absolute():
        return ""
    while raw.startswith("./"):
        raw = raw[2:]
    raw = raw.strip("/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _parse_name_status_z(data: bytes) -> GitNameStatusResult:
    if not data:
        return GitNameStatusResult(())
    raw_tokens = data.split(b"\0")
    if raw_tokens[-1] != b"" or any(token == b"" for token in raw_tokens[:-1]):
        return GitNameStatusResult((), GitNameStatusProblem.MALFORMED)
    tokens = [token.decode("utf-8", errors="surrogateescape") for token in raw_tokens[:-1]]
    entries: list[ChangedEntry] = []
    index = 0
    while index < len(tokens):
        code = tokens[index]
        index += 1
        if not re.fullmatch(r"[A-Z](?:[0-9]{1,3})?", code):
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.MALFORMED)
        change_type = code[0]
        if change_type == "U":
            path = normalize_repo_path(tokens[index]) if index < len(tokens) else ""
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.UNMERGED, path)
        if change_type in {"X", "B"}:
            path = normalize_repo_path(tokens[index]) if index < len(tokens) else ""
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.UNSUPPORTED, path)
        if change_type in {"R", "C"}:
            if index + 1 >= len(tokens):
                return GitNameStatusResult(tuple(entries), GitNameStatusProblem.MALFORMED)
            old_path = normalize_repo_path(tokens[index])
            path = normalize_repo_path(tokens[index + 1])
            index += 2
            if not path or not old_path:
                return GitNameStatusResult(tuple(entries), GitNameStatusProblem.INVALID_PATH, path or old_path)
            entries.append(("renamed" if change_type == "R" else "copied", path, old_path))
            continue
        if change_type not in {"A", "M", "D", "T"} or len(code) != 1:
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.UNSUPPORTED)
        if index >= len(tokens):
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.MALFORMED)
        path = normalize_repo_path(tokens[index])
        index += 1
        if not path:
            return GitNameStatusResult(tuple(entries), GitNameStatusProblem.INVALID_PATH)
        mapping = {"A": "added", "M": "modified", "D": "deleted", "T": "modified"}
        entries.append((mapping[change_type], path, ""))
    return GitNameStatusResult(tuple(entries))


def _name_status_failure_state(selected: RepoTarget, result: GitNameStatusResult) -> RepoGitState:
    messages = {
        GitNameStatusProblem.MALFORMED: "returned malformed name-status data",
        GitNameStatusProblem.INVALID_PATH: "returned an invalid repository path",
        GitNameStatusProblem.UNMERGED: "contains an unresolved merge path",
        GitNameStatusProblem.UNSUPPORTED: "returned an unsupported change type",
    }
    problem = result.problem or GitNameStatusProblem.MALFORMED
    suffix = f": {result.path}" if result.path else ""
    return RepoGitState(
        False,
        f"{selected.display_path}/ {messages[problem]}{suffix}",
        selected.id,
        selected.display_path,
        problem.value,
    )


def _empty_tree_oid(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "hash-object", "-t", "tree", "--stdin"],
            cwd=repo,
            input=b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return ""
    object_id = result.stdout.decode("ascii", errors="ignore").strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id):
        return ""
    return object_id


def repo_changed_entries(root: Path, target: RepoTarget | None = None) -> tuple[list[ChangedEntry], RepoGitState]:
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return [], state
    assert selected is not None
    repo = selected.root_path
    seen: set[ChangedEntry] = set()
    changes: list[ChangedEntry] = []
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        head = None
    diff_base = "HEAD" if head is not None and head.returncode == 0 else _empty_tree_oid(repo)
    if not diff_base:
        return [], RepoGitState(False, f"{selected.display_path}/ cannot construct repository baseline", selected.id, selected.display_path)
    diff_commands = (
        ["git", "-c", "core.quotePath=false", "diff", "--name-status", "-z", "--find-renames", diff_base],
        ["git", "-c", "core.quotePath=false", "diff", "--cached", "--name-status", "-z", "--find-renames", diff_base],
    )
    for command in diff_commands:
        try:
            result = subprocess.run(
                command,
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            result = None
        if result is None or result.returncode != 0:
            return [], RepoGitState(False, f"{selected.display_path}/ cannot compare repository baseline to index and working tree", selected.id, selected.display_path)
        parsed = _parse_name_status_z(result.stdout)
        if parsed.problem is not None:
            return [], _name_status_failure_state(selected, parsed)
        for item in parsed.entries:
            if item not in seen:
                seen.add(item)
                changes.append(item)
    untracked = _git_bytes_result(repo, ["ls-files", "--others", "--exclude-standard", "-z"])
    if untracked.problem is not None:
        return [], RepoGitState(False, f"{selected.display_path}/ cannot read untracked paths",
                                selected.id, selected.display_path, untracked.problem.value)
    for token in untracked.data.split(b"\0"):
        if not token:
            continue
        path = normalize_repo_path(token.decode("utf-8", errors="surrogateescape"))
        if not path:
            continue
        item = ("untracked", path, "")
        if item not in seen:
            seen.add(item)
            changes.append(item)
    return changes, state


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _git_bytes_result(repo: Path, args: list[str]) -> _GitBytesResult:
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return _GitBytesResult(b"", GitObservationProblem.COMMAND_FAILED)
    if result.returncode != 0:
        return _GitBytesResult(b"", GitObservationProblem.COMMAND_FAILED)
    return _GitBytesResult(result.stdout)


def _git_index_records(repo: Path) -> tuple[dict[str, dict[str, str]], GitObservationProblem | None]:
    records: dict[str, dict[str, str]] = {}
    result = _git_bytes_result(repo, ["ls-files", "--stage", "-z"])
    if result.problem is not None:
        return {}, result.problem
    for token in result.data.split(b"\0"):
        if not token or b"\t" not in token:
            if token:
                return {}, GitObservationProblem.INDEX_MALFORMED
            continue
        raw_identity, raw_path = token.split(b"\t", 1)
        parts = raw_identity.decode("ascii", errors="ignore").split()
        path = normalize_repo_path(raw_path.decode("utf-8", errors="surrogateescape"))
        if len(parts) != 3 or not path or parts[2] != "0":
            return {}, GitObservationProblem.INDEX_MALFORMED
        records[path] = {"mode": parts[0], "object": parts[1]}
    return records, None


def _stat_probe(path: Path) -> dict[str, Any]:
    try:
        file_stat = os.lstat(path)
    except FileNotFoundError:
        return {"kind": "missing"}
    probe: dict[str, Any] = {
        "mode": f"{stat.S_IMODE(file_stat.st_mode):04o}",
        "size": int(file_stat.st_size),
        "mtime_ns": int(file_stat.st_mtime_ns),
        "ctime_ns": int(file_stat.st_ctime_ns),
    }
    if stat.S_ISLNK(file_stat.st_mode):
        probe.update({"kind": "symlink", "target": os.readlink(path)})
    elif stat.S_ISREG(file_stat.st_mode):
        probe["kind"] = "file"
    else:
        probe["kind"] = "other"
    return probe


def repo_file_state_records(
    root: Path,
    *,
    paths: list[str],
    target: RepoTarget | None = None,
    previous: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], RepoGitState]:
    """Return content identities without rereading clean tracked files.

    Git object IDs identify clean tracked files. Dirty and untracked files use a
    stat probe to reuse the previous content digest when their working-tree
    state has not changed.
    """
    selected = _target(root, target)
    state = repo_git_state(root, selected)
    if not state.available:
        return {}, state
    assert selected is not None
    repo = selected.root_path
    index_records, index_problem = _git_index_records(repo)
    if index_problem is not None:
        return {}, RepoGitState(
            False,
            f"{selected.display_path}/ cannot read repository index state",
            selected.id,
            selected.display_path,
            index_problem.value,
        )
    changes, change_state = repo_changed_entries(root, selected)
    if not change_state.available:
        return {}, change_state
    changes_by_path: dict[str, list[dict[str, str]]] = {}
    for change, path, old_path in changes:
        if path:
            item = {"change": change}
            if old_path:
                item["old_path"] = old_path
            changes_by_path.setdefault(path, []).append(item)
    previous = previous or {}
    records: dict[str, dict[str, Any]] = {}
    for path in sorted({normalized for value in paths if (normalized := normalize_repo_path(value))}):
        index = index_records.get(path, {})
        path_changes = sorted(changes_by_path.get(path, []), key=lambda item: (item.get("change", ""), item.get("old_path", "")))
        if index and not path_changes:
            records[path] = {
                "path": path,
                "source": "git_index",
                "mode": index.get("mode", ""),
                "object": index.get("object", ""),
            }
            continue

        try:
            file_stat = _stat_probe(repo / path)
        except OSError as exc:
            return {}, RepoGitState(False, f"{path}: {exc}", selected.id, selected.display_path,
                                    GitObservationProblem.FILE_UNREADABLE.value)
        probe = {"index": index, "changes": path_changes, "stat": file_stat}
        old = previous.get(path)
        if isinstance(old, dict) and old.get("probe") == probe and old.get("source") == "working_tree":
            records[path] = old
            continue

        record: dict[str, Any] = {
            "path": path,
            "source": "working_tree",
            "probe": probe,
        }
        kind = str(probe["stat"].get("kind") or "")
        if kind == "file":
            try:
                content = (repo / path).read_bytes()
                if _stat_probe(repo / path) != file_stat:
                    return {}, RepoGitState(False, f"{path}: source changed while it was being read",
                                            selected.id, selected.display_path, GitObservationProblem.FILE_CHANGED.value)
                record["content_sha256"] = _sha256_bytes(content)
            except OSError as exc:
                return {}, RepoGitState(False, f"{path}: {exc}", selected.id, selected.display_path,
                                        GitObservationProblem.FILE_UNREADABLE.value)
        elif kind == "symlink":
            record["symlink_target"] = str(probe["stat"].get("target") or "")
        records[path] = record
    return records, state
