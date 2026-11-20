from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .io import RepoctlError
from .markdown import find_section


@dataclass(frozen=True)
class BacklogItem:
    id: str
    title: str
    raw: str
    line_start: int
    line_end: int
    start: int
    end: int

    def to_dict(self) -> dict[str, str | int]:
        return {"id": self.id, "title": self.title, "raw": self.raw, "line_start": self.line_start, "line_end": self.line_end}


def _backlog_id(raw: str) -> str:
    return "BL-" + sha256(raw.encode("utf-8")).hexdigest()[:12]


def read_backlog_items(board_text: str) -> list[BacklogItem]:
    section = find_section(board_text, "Backlog")
    items: list[BacklogItem] = []
    current_start: int | None = None
    current_end: int | None = None
    current_line_start = 0
    offset = section.body_start
    line_no = board_text.count("\n", 0, section.body_start) + 1

    def flush() -> None:
        if current_start is None or current_end is None:
            return
        block = board_text[current_start:current_end]
        raw = block.rstrip("\n")
        if not raw:
            return
        first = raw.splitlines()[0].strip()
        title = first[2:].strip() if first.startswith("- ") else first
        line_end = current_line_start + len(raw.splitlines()) - 1
        items.append(BacklogItem(_backlog_id(raw), title, raw, current_line_start, line_end, current_start, current_end))

    for line in board_text[section.body_start : section.end].splitlines(keepends=True):
        if line.startswith("- ") and line.strip():
            flush()
            current_start = offset
            current_end = offset + len(line)
            current_line_start = line_no
        elif current_start is not None and (line.startswith((" ", "\t")) or not line.strip()):
            current_end = offset + len(line)
        elif current_start is not None:
            flush()
            current_start = None
            current_end = None
        offset += len(line)
        line_no += 1
    flush()
    return items


def resolve_backlog_item(board_text: str, backlog_id: str) -> BacklogItem:
    matches = [item for item in read_backlog_items(board_text) if item.id == backlog_id]
    if not matches:
        raise RepoctlError(f"backlog item not found: {backlog_id}", code="backlog_not_found")
    if len(matches) > 1:
        raise RepoctlError(f"backlog item id is ambiguous: {backlog_id}", code="duplicate_backlog_id")
    return matches[0]


def append_backlog_item(board_text: str, title: str, body: str = "") -> str:
    if not title.strip() or any(char in title for char in "\r\n\x00"):
        raise RepoctlError("Backlog title must be a nonempty single line", code="invalid_title")
    section = find_section(board_text, "Backlog")
    raw = f"- {title.strip()}"
    body = body.strip("\n")
    if body:
        raw += "\n" + "\n".join(f"  {line}" if line else "" for line in body.splitlines())
    insert = raw + "\n"
    prefix = board_text[: section.end]
    suffix = board_text[section.end :]
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix[section.body_start :] and not prefix.endswith("\n\n"):
        insert = "\n" + insert
    return prefix + insert + suffix


def remove_backlog_item(board_text: str, backlog_id: str) -> tuple[str, BacklogItem]:
    item = resolve_backlog_item(board_text, backlog_id)
    return board_text[: item.start] + board_text[item.end :], item
