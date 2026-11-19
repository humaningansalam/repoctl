"""Task documents and edits. No product Git state is changed or inferred here."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .io import Problem, RepoctlError
from .markdown import (
    append_section_entry, find_section, labeled_list_lines, parse_frontmatter,
    parse_labeled_list_section, replace_frontmatter_line,
    section_ranges,
)

LIVE = {"todo", "doing", "blocked"}
NON_LIVE = {"done", "canceled"}
STATUSES = LIVE | NON_LIVE


@dataclass(frozen=True)
class Task:
    path: Path
    rel_path: str
    frontmatter: dict[str, Any]
    body: str
    archived: bool = False
    source_text: str | None = None

    @property
    def id(self) -> str:
        return str(self.frontmatter.get("id") or "")

    @property
    def title(self) -> str:
        return str(self.frontmatter.get("title") or "")

    @property
    def status(self) -> str:
        return str(self.frontmatter.get("status") or "")

    @property
    def parent(self) -> str:
        return str(self.frontmatter.get("parent") or "")

    @property
    def repo_id(self) -> str:
        return str(self.frontmatter.get("repo_id") or "")

    def to_list_dict(self, *, compact: bool = False) -> dict[str, Any]:
        result = {
            "id": self.id, "path": self.rel_path, "title": self.title,
            "status": self.status, "parent": self.parent, "repo_id": self.repo_id,
            "area": str(self.frontmatter.get("area") or ""), "archived": self.archived,
        }
        if not compact:
            result.update({
                "owner": str(self.frontmatter.get("owner") or ""),
                "repo_ref": str(self.frontmatter.get("repo_ref") or ""),
                "depends_on": self.frontmatter.get("depends_on") or [],
                "completed_at": str(self.frontmatter.get("completed_at") or ""),
            })
        return result


def _read_text(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as source:
        return source.read()


def _contained_path(root: Path, path: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise RepoctlError("Task path is outside the workspace", code="task_path_outside_workspace", path=str(path)) from exc
    if path.is_symlink():
        raise RepoctlError("Task files cannot be symlinks", code="invalid_task_path", path=str(path))


def load_task(path: Path, root: Path, *, archived: bool = False) -> Task:
    _contained_path(root, path)
    text = _read_text(path)
    fields, body = parse_frontmatter(text)
    return Task(path, path.relative_to(root).as_posix(), fields, body, archived, source_text=text)


def load_tasks(root: Path, *, include_archived: bool = False) -> list[Task]:
    tasks: list[Task] = []
    folders = [(root / "docs/tasks", False)]
    if include_archived:
        folders.append((root / "docs/archive/tasks", True))
    for folder, archived in folders:
        for path in sorted(folder.glob("*.md")):
            if path.name.endswith("TEMPLATE.md"):
                continue
            task = load_task(path, root, archived=archived)
            if task.id or path.name.startswith("T-"):
                tasks.append(task)
    return tasks


def normalize_task_id(value: str) -> str:
    """Accept exact IDs, and extract an ID from a conventional Task filename."""
    candidate = str(value).strip()
    if not candidate or any(character in candidate for character in "\x00\r\n"):
        raise RepoctlError("Task id is empty or contains a control character", code="invalid_task_id")
    if candidate.endswith(".md"):
        return candidate.replace("\\", "/").rsplit("/", 1)[-1][:-3].split("--", 1)[0]
    return candidate


def _read_task_ids(path: Path, root: Path) -> set[str]:
    """Read identity fields only; the selected document is parsed in full."""
    _contained_path(root, path)
    identities: set[str] = set()
    with path.open("rb") as source:
        if source.readline() not in {b"---\n", b"---\r\n"}:
            return identities
        while line := source.readline():
            if line in {b"---\n", b"---\r\n", b"---"}:
                break
            key, separator, _value = line.partition(b":")
            if separator and key.strip() == b"id":
                fields, _body = parse_frontmatter("---\n" + line.decode("utf-8").rstrip("\r\n") + "\n---\n")
                if identity := str(fields.get("id") or ""):
                    identities.add(identity)
    return identities


def resolve_task(root: Path, task_id: str) -> Task:
    raw = str(task_id).strip()
    normalize_task_id(raw)
    folders = (root / "docs/tasks", root / "docs/archive/tasks")
    reference = raw.replace("\\", "/").removeprefix("./")

    def read(path: Path) -> Task:
        return load_task(path, root, archived=path.parent == folders[1])

    if reference.endswith(".md"):
        if "/" in reference:
            path = Path(reference)
            path = path if path.is_absolute() else root / path
            if path.parent not in folders:
                raise RepoctlError("Task path is outside Task directories", code="invalid_task_path", path=reference)
            if not path.is_file() and not path.is_symlink():
                raise RepoctlError(f"Task not found: {raw}", code="task_not_found")
            return read(path)
        named = [folder / reference for folder in folders if (folder / reference).is_file() or (folder / reference).is_symlink()]
        if len(named) > 1:
            raise RepoctlError(f"Task reference is ambiguous: {raw}", code="duplicate_task_id")
        if named:
            return read(named[0])
    matches: list[Path] = []
    # Generated and human-authored filenames have equal standing. Inspect all
    # identities, then read the selected body; unrelated damage does not block it.
    for folder in folders:
        for path in sorted(folder.glob("*.md")):
            if path.name.endswith("TEMPLATE.md"):
                continue
            try:
                identities = _read_task_ids(path, root)
            except (RepoctlError, OSError, UnicodeError):
                continue
            if raw in identities:
                matches.append(path)
    if not matches:
        raise RepoctlError(f"Task not found: {raw}", code="task_not_found")
    if len(matches) > 1:
        raise RepoctlError(f"Task id is ambiguous: {raw}", code="duplicate_task_id")
    task = read(matches[0])
    if task.id != raw:
        raise RepoctlError("Task identity changed while it was being selected; retry", code="task_changed", path=task.rel_path)
    return task


def live_tasks(tasks: list[Task]) -> list[Task]:
    return [task for task in tasks if task.status in LIVE and not task.archived]


def children_by_parent(tasks: list[Task]) -> dict[str, list[Task]]:
    result: dict[str, list[Task]] = {}
    for task in tasks:
        if task.parent:
            result.setdefault(task.parent, []).append(task)
    return result


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _plan(root: Path, text: str, *, task: Task | None, new_path: str, original_text: str | None = None) -> dict[str, Any]:
    fields, body = parse_frontmatter(text)
    archived = new_path.startswith("docs/archive/tasks/")
    return {
        "task_id": str(fields["id"]),
        "task": task or Task(root / new_path, new_path, fields, body, archived, source_text=text),
        "old_path": task.rel_path if task is not None else "",
        "new_path": new_path,
        "text": text,
        "original_text": original_text,
        "archived": archived,
    }


def create_task_file(
    root: Path, *, title: str, slug: str | None = None, area: str = "",
    owner: str = "unassigned", parent: str = "", repo_ref: str = "",
    repo_id: str = "", depends_on: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Prepare a new document; publishing it is the caller's single write."""
    title = title.strip()
    if not title or "\n" in title or "\r" in title:
        raise RepoctlError("Task title must be a non-empty line", code="invalid_title")
    if slug is None:
        slug = "-".join(re.findall(r"\w+", unicodedata.normalize("NFKC", title).casefold())).replace("_", "-").strip("-") or "task"
    if not slug or slug in {".", ".."} or any(character in slug for character in "/\\\x00\r\n"):
        raise RepoctlError("Task slug must be a filename component", code="invalid_slug")
    task_id = "T-" + datetime.now(UTC).strftime("%Y%m%d%H%M%SZ") + "-" + uuid4().hex[:8]
    fields: dict[str, Any] = {
        "id": task_id, "title": title, "status": "todo", "owner": owner,
        "repo_id": repo_id, "repo_ref": repo_ref, "created": utc_stamp(),
        "area": area, "parent": parent, "depends_on": list(depends_on),
    }
    front = "---\n" + "".join(f"{key}: {json.dumps(value, ensure_ascii=False)}\n" for key, value in fields.items()) + "---\n"
    body = (
        f"\n# {task_id} — {title}\n\n## Goal\n\n{title}\n\n"
        "## Handoff\n\n- Next exact step:\n- Done when:\n"
    )
    return _plan(root, front + body, task=None, new_path=f"docs/tasks/{task_id}--{slug}.md")


def _live_task(root: Path, task_id: str) -> Task:
    task = resolve_task(root, task_id)
    if not task.id:
        raise RepoctlError("Task has no id", code="missing_task_id", path=task.rel_path)
    if task.status not in STATUSES:
        raise RepoctlError(f"Unknown Task status: {task.status}", code="invalid_task_status", path=task.rel_path)
    if task.archived or task.status in NON_LIVE:
        raise RepoctlError("Task is already closed", code="task_not_live", path=task.rel_path)
    return task


def _status_plan(root: Path, task: Task, status: str, *, note: str = "") -> dict[str, Any]:
    original = task.source_text if task.source_text is not None else _read_text(task.path)
    text = replace_frontmatter_line(original, "status", status)
    new_path = task.rel_path
    if status == "doing" and not task.frontmatter.get("started_at"):
        text = replace_frontmatter_line(text, "started_at", json.dumps(utc_stamp()))
    if status in NON_LIVE:
        text = replace_frontmatter_line(text, "completed_at", json.dumps(utc_stamp()))
        new_path = f"docs/archive/tasks/{task.path.name}"
        target = root / new_path
        if target != task.path and (target.exists() or target.is_symlink()):
            raise RepoctlError("Archive target already exists", code="archive_task_conflict", path=new_path)
    if note:
        text = _append_note(text, note)
    return _plan(root, text, task=task, new_path=new_path, original_text=original)


def start_task(root: Path, task_id: str) -> dict[str, Any]:
    return _status_plan(root, _live_task(root, task_id), "doing")


def finish_task(root: Path, task_id: str, *, summary: str = "") -> dict[str, Any]:
    return _status_plan(root, _live_task(root, task_id), "done", note=summary)


def cancel_task(root: Path, task_id: str, *, reason: str = "") -> dict[str, Any]:
    return _status_plan(root, _live_task(root, task_id), "canceled", note=reason)


def block_task(root: Path, task_id: str, *, reason: str = "") -> dict[str, Any]:
    return _status_plan(root, _live_task(root, task_id), "blocked", note=reason)


def _append_note(text: str, message: str) -> str:
    lines = message.replace("\r\n", "\n").strip("\n").splitlines()
    if not lines or not any(line.strip() for line in lines):
        raise RepoctlError("Task log message is empty", code="empty_task_log")
    entry = f"- {utc_stamp()}: {lines[0]}" + "".join("\n  " + line for line in lines[1:])
    return append_section_entry(text, "Execution Log", entry)


def append_task_log(root: Path, task_id: str, message: str) -> dict[str, Any]:
    task = _live_task(root, task_id)
    original = task.source_text if task.source_text is not None else _read_text(task.path)
    return _plan(root, _append_note(original, message), task=task, new_path=task.rel_path, original_text=original)


def task_handoff_body(task: Task) -> str | None:
    headings = ("Last Active Handoff", "Handoff") if task.status in NON_LIVE else ("Handoff",)
    for heading in headings:
        try:
            section = find_section(task.body, heading)
        except RepoctlError as exc:
            if exc.code == "missing_section":
                continue
            raise
        return task.body[section.body_start:section.end].strip()
    return None


def task_discovery_values(task: Task) -> dict[str, list[str]]:
    try:
        fields = parse_labeled_list_section(task.body, "Discovery", ("Chosen files", "Notes"))
    except RepoctlError as exc:
        if exc.code == "missing_section":
            return {"Chosen files": [], "Notes": []}
        raise
    result: dict[str, list[str]] = {}
    for key in ("Chosen files", "Notes"):
        result[key] = []
        for raw in fields.get(key, []):
            value = raw.strip()
            if len(value) >= 2 and value[0] == value[-1] == chr(96):
                value = value[1:-1]
            if value and value.casefold() not in {"none", "none yet", "n/a", "pending"} and value not in result[key]:
                result[key].append(value)
    return result


def _set_chosen_paths(text: str, paths: list[str]) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    replacement = "- Chosen files:" + newline + "".join(f"  - {chr(96)}{path}{chr(96)}{newline}" for path in paths)
    try:
        entries = labeled_list_lines(text, "Discovery", ("Chosen files",))
    except RepoctlError as exc:
        if exc.code != "missing_section":
            raise
        return append_section_entry(text, "Discovery", replacement)
    if not entries:
        section = find_section(text, "Discovery")
        return text[:section.body_start] + newline + replacement + text[section.body_start:]
    pieces: list[str] = []
    offset = 0
    for index, (_label, _value, start, end) in enumerate(entries):
        pieces.append(text[offset:start])
        if index == 0:
            pieces.append(replacement)
        offset = end
    pieces.append(text[offset:])
    return "".join(pieces)


def update_task_discovery(
    root: Path, task_id: str, *, chosen: list[str] | None = None,
    replace_chosen: list[str] | None = None, note: str = "", reason: str = "",
) -> dict[str, Any]:
    task = _live_task(root, task_id)
    if chosen is not None and replace_chosen is not None:
        raise RepoctlError("Choose append or replacement, not both", code="ambiguous_chosen_update")
    previous = task_discovery_values(task)
    paths = list(dict.fromkeys(replace_chosen if replace_chosen is not None else previous["Chosen files"] + (chosen or [])))
    for path in paths:
        relative = PurePosixPath(path)
        if not path or relative.is_absolute() or ".." in relative.parts or any(character in path for character in "\x00\r\n"):
            raise RepoctlError("Chosen paths must be workspace-relative", code="invalid_discovery_path", path=path)
    original = task.source_text if task.source_text is not None else _read_text(task.path)
    text = _set_chosen_paths(original, paths) if chosen is not None or replace_chosen is not None else original
    if note:
        text = append_section_entry(text, "Discovery", "- Notes: " + note.replace("\n", "\n  "))
    if reason:
        text = _append_note(text, reason)
    result = _plan(root, text, task=task, new_path=task.rel_path, original_text=original)
    result["discovery"] = {"chosen_files": paths, "notes": previous["Notes"] + ([note] if note else [])}
    return result


def validate_tasks(tasks: list[Task], *, include_archived_warnings: bool = False) -> list[Problem]:
    """Diagnose document identity and relations without deciding work readiness."""
    problems: list[Problem] = []
    by_id: dict[str, Task] = {}
    for task in tasks:
        if not task.id:
            problems.append(Problem("error", "missing_task_id", "Task has no id", task.rel_path))
            continue
        if task.id in by_id:
            problems.append(Problem("error", "duplicate_task_id", f"Duplicate Task id: {task.id}", task.rel_path))
        by_id[task.id] = task
        if task.status not in STATUSES:
            problems.append(Problem("warning", "unknown_status", f"Unknown Task status: {task.status}", task.rel_path))
        headings: dict[str, int] = {}
        for section in section_ranges(task.body):
            headings[section.heading] = headings.get(section.heading, 0) + 1
        for heading, count in headings.items():
            if count > 1 and heading in {"## Goal", "## Handoff", "## Last Active Handoff", "## Discovery", "## Context Docs", "## Execution Log"}:
                problems.append(Problem("error", "duplicate_section", f"Duplicate section: {heading}", task.rel_path))
    for task in tasks:
        if task.parent and task.parent not in by_id:
            problems.append(Problem("warning", "missing_parent", f"Parent Task not found: {task.parent}", task.rel_path))
        path: list[str] = []
        current = task.id
        while current in by_id:
            if current in path:
                if current == task.id:
                    problems.append(Problem("warning", "parent_cycle", "Parent relation is cyclic: " + " -> ".join([*path, current]), task.rel_path))
                break
            path.append(current)
            current = by_id[current].parent
    return problems
