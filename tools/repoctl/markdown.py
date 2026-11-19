from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from typing import Any

from .io import RepoctlError


@dataclass(frozen=True)
class SectionRange:
    heading: str
    start: int
    body_start: int
    end: int


def _frontmatter_bounds(text: str) -> tuple[int, int] | None:
    opening = re.match(r"\A---\r?\n", text)
    if opening is None:
        return None
    closing = re.search(r"^---(?:\r?\n|$)", text[opening.end():], re.MULTILINE)
    if closing is None:
        raise RepoctlError("frontmatter closing delimiter missing", code="invalid_frontmatter")
    return opening.end(), opening.end() + closing.end()


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    try:
        return json.loads(value)
    except ValueError:
        pass
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if value.startswith("[") and value.endswith("]"):
        return [part.strip().strip("'") for part in next(csv.reader([value[1:-1]], skipinitialspace=True)) if part.strip()]
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    bounds = _frontmatter_bounds(text)
    if bounds is None:
        return {}, text
    start, end = bounds
    data: dict[str, Any] = {}
    for line in text[start:end].splitlines()[:-1]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        key = key.strip()
        if not separator or not key:
            raise RepoctlError("expected a frontmatter key and value", code="invalid_frontmatter")
        if key in data:
            raise RepoctlError(f"duplicate frontmatter key: {key}", code="duplicate_frontmatter_key")
        data[key] = _parse_scalar(value)
    return data, text[end:]


def replace_frontmatter_line(text: str, key: str, value: str) -> str:
    """Set one scalar field while preserving unrelated source text."""
    parse_frontmatter(text)
    bounds = _frontmatter_bounds(text)
    newline = "\r\n" if "\r\n" in text else "\n"
    if bounds is None:
        return f"---{newline}{key}: {value}{newline}---{newline}{text}"
    start, end = bounds
    offset = start
    lines = text[start:end].splitlines(keepends=True)
    for line in lines[:-1]:
        if line.partition(":")[0].strip() == key:
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            return text[:offset] + f"{key}: {value}" + ending + text[offset + len(line):]
        offset += len(line)
    closing_start = end - len(lines[-1])
    return text[:closing_start] + f"{key}: {value}{newline}" + text[closing_start:]


def _without_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    if in_comment:
        if "-->" not in line:
            return "", True
        line = line.split("-->", 1)[1]
        in_comment = False
    while "<!--" in line:
        before, after = line.split("<!--", 1)
        if "-->" in after:
            line = before + after.split("-->", 1)[1]
        else:
            return before, True
    return line, in_comment


def _heading_ranges(text: str) -> list[tuple[str, int, int]]:
    ranges: list[tuple[str, int, int]] = []
    offset = 0
    fence_char, fence_length = "", 0
    in_comment = False
    for line in text.splitlines(keepends=True):
        if fence_char:
            close = re.match(r"^ {0,3}(\x60{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
            if close and close.group(1)[0] == fence_char and len(close.group(1)) >= fence_length and not close.group(2).strip():
                fence_char, fence_length = "", 0
            offset += len(line)
            continue
        visible, in_comment = _without_comments(line, in_comment)
        marker = re.match(r"^ {0,3}(\x60{3,}|~{3,})(.*)$", visible.rstrip("\r\n"))
        if marker:
            sequence, suffix = marker.groups()
            if not fence_char:
                fence_char, fence_length = sequence[0], len(sequence)
            elif sequence[0] == fence_char and len(sequence) >= fence_length and not suffix.strip():
                fence_char, fence_length = "", 0
            offset += len(line)
            continue
        if not fence_char:
            heading = re.match(r"^ {0,3}##[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$", visible.rstrip("\r\n"))
            if heading:
                ranges.append(("## " + heading.group(1).strip(), offset, offset + len(line)))
        offset += len(line)
    return ranges


def section_ranges(text: str) -> list[SectionRange]:
    headings = _heading_ranges(text)
    return [
        SectionRange(heading, start, body_start, headings[index + 1][1] if index + 1 < len(headings) else len(text))
        for index, (heading, start, body_start) in enumerate(headings)
    ]


def find_section(text: str, heading: str) -> SectionRange:
    wanted = heading if heading.startswith("## ") else f"## {heading}"
    matches = [section for section in section_ranges(text) if section.heading == wanted]
    if not matches:
        raise RepoctlError(f"section missing: {wanted}", code="missing_section")
    if len(matches) > 1:
        raise RepoctlError(f"duplicate section: {wanted}", code="duplicate_section")
    return matches[0]


def has_section(text: str, heading: str) -> bool:
    try:
        find_section(text, heading)
    except RepoctlError as exc:
        if exc.code == "missing_section":
            return False
        raise
    return True


def labeled_list_lines(text: str, heading: str, labels: tuple[str, ...]) -> list[tuple[str, str, int, int]]:
    """Return field values and their source-line spans, excluding examples."""
    section = find_section(text, heading)
    entries: list[tuple[str, str, int, int]] = []
    current = ""
    fence_char, fence_length = "", 0
    in_comment = False
    offset = section.body_start
    for line in text[section.body_start:section.end].splitlines(keepends=True):
        start, offset = offset, offset + len(line)
        stripped = line.strip()
        marker = re.match(r"^(\x60{3,}|~{3,})(.*)$", stripped)
        if fence_char:
            if marker and marker.group(1)[0] == fence_char and len(marker.group(1)) >= fence_length and not marker.group(2).strip():
                fence_char, fence_length = "", 0
            continue
        visible, in_comment = _without_comments(line, in_comment)
        stripped = visible.strip()
        marker = re.match(r"^(\x60{3,}|~{3,})(.*)$", stripped)
        if marker:
            sequence, _suffix = marker.groups()
            fence_char, fence_length = sequence[0], len(sequence)
            current = ""
            continue
        if not stripped:
            continue
        if visible.startswith("- "):
            label, separator, value = visible[2:].partition(":")
            current = label if separator and label in labels else ""
            if current:
                entries.append((current, value.strip(), start, offset))
        elif current and visible.startswith(("  ", "\t")) and stripped.startswith("- "):
            entries.append((current, stripped[2:].strip(), start, offset))
        else:
            current = ""
    return entries


def parse_labeled_list_section(text: str, heading: str, labels: tuple[str, ...]) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    for label, value, _start, _end in labeled_list_lines(text, heading, labels):
        values = parsed.setdefault(label, [])
        if value:
            values.append(value)
    return parsed


def replace_section(text: str, heading: str, body: str) -> str:
    section = find_section(text, heading)
    newline = "\r\n" if "\r\n" in text else "\n"
    normalized = body.replace("\r\n", "\n").replace("\n", newline).rstrip("\r\n") + newline
    if text[section.end:]:
        normalized += newline
    return text[:section.body_start] + newline + normalized + text[section.end:]


def append_section_entry(text: str, heading: str, entry: str) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    entry = entry.replace("\r\n", "\n").replace("\n", newline).rstrip("\r\n") + newline
    try:
        section = find_section(text, heading)
    except RepoctlError as exc:
        if exc.code != "missing_section":
            raise
        separator = "" if text.endswith(newline * 2) else newline if text.endswith(newline) else newline * 2
        return text + separator + f"## {heading}{newline}{newline}" + entry
    prefix, suffix = text[:section.end], text[section.end:]
    if not prefix.endswith(newline):
        prefix += newline
    return prefix + entry + (newline if suffix else "") + suffix
