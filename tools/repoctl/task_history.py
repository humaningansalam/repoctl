from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .io import Problem, RepoctlError
from .tasks import Task, load_task, resolve_task, task_discovery_values


@dataclass(frozen=True)
class TaskHistoryEntry:
    task_id: str
    repo_id: str
    path: str
    text: str
    title: str
    status: str
    completed_at: str
    chosen_paths: tuple[str, ...]

    @property
    def content_sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "repo_id": self.repo_id,
            "path": self.path,
            "title": self.title,
            "status": self.status,
            "completed_at": self.completed_at,
            "chosen_paths": list(self.chosen_paths),
            "content_sha256": self.content_sha256,
        }


def _entry_from_task(task: Task) -> TaskHistoryEntry | None:
    if task.status not in {"done", "canceled"}:
        return None
    if not task.id:
        raise RepoctlError("Completed Task has no id", code="missing_task_id", path=task.rel_path)
    if task.source_text is None:
        raise ValueError("Task history requires the loaded document source")
    discovery = task_discovery_values(task)
    return TaskHistoryEntry(
        task.id,
        task.repo_id,
        task.rel_path,
        task.source_text,
        task.title,
        task.status,
        str(task.frontmatter.get("completed_at") or ""),
        tuple(discovery.get("Chosen files", [])),
    )


def _read_entry(root: Path, path: Path) -> TaskHistoryEntry | None:
    return _entry_from_task(load_task(path, root, archived=path.parent == root / "docs/archive/tasks"))


def read_task_history(
    root: Path, *, repo_id: str
) -> tuple[list[TaskHistoryEntry], list[Problem]]:
    entries: list[TaskHistoryEntry] = []
    problems: list[Problem] = []
    seen: set[str] = set()
    for directory in (root / "docs/tasks", root / "docs/archive/tasks"):
        for path in sorted(directory.glob("*.md")):
            if path.name.endswith("TEMPLATE.md"):
                continue
            try:
                entry = _read_entry(root, path)
                if entry is None or entry.repo_id != repo_id:
                    continue
                if entry.task_id in seen:
                    problems.append(
                        Problem(
                            "warning",
                            "task_history_ambiguous",
                            "duplicate completed Task ID",
                            entry.path,
                        )
                    )
                seen.add(entry.task_id)
                entries.append(entry)
            except (
                OSError,
                UnicodeError,
                ValueError,
                RuntimeError,
                RepoctlError,
            ) as exc:
                problems.append(
                    Problem(
                        "warning",
                        "task_history_unreadable",
                        str(exc),
                        path.relative_to(root).as_posix(),
                    )
                )
    return entries, problems


def lookup_task_history(root: Path, repo_id: str, task_id: str) -> TaskHistoryEntry | None:
    try:
        task = resolve_task(root, task_id)
    except RepoctlError as exc:
        if exc.code == "task_not_found":
            return None
        raise
    if task.repo_id != repo_id:
        return None
    return _entry_from_task(task)


def search_task_history(
    root: Path,
    repo_id: str,
    *,
    query_terms: Iterable[str] = (),
    task_ids: Iterable[str] = (),
    limit: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[Problem]]:
    entries, problems = read_task_history(root, repo_id=repo_id)
    terms = tuple(
        dict.fromkeys(str(term).casefold() for term in query_terms if str(term))
    )
    wanted_ids = set(task_ids)
    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for entry in entries:
        body = entry.text.casefold()
        matched = tuple(term for term in terms if term in body)
        exact = entry.task_id in wanted_ids
        if (terms or wanted_ids) and not matched and not exact:
            continue
        rank = (not exact, -len(matched), entry.task_id)
        ranked.append(
            (rank, {"entry": entry, "matched_terms": matched, "exact_task_id": exact})
        )
    ranked.sort(key=lambda item: item[0])
    matches = [item[1] for item in ranked[: max(0, limit)]]
    summary = {
        "scanned_task_count": len(entries),
        "matched_task_count": len(ranked),
        "returned_count": len(matches),
        "truncated": len(ranked) > len(matches),
    }
    return matches, summary, problems
