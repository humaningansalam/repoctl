"""Bounded, source-linked observation of explicitly referenced Task Context Docs."""

from __future__ import annotations

import hashlib
import posixpath
import re
from urllib.parse import unquote
from pathlib import Path, PurePosixPath
from typing import Any

from .io import RepoctlError
from .markdown import find_section


MAX_REFERENCES = 32
MAX_DOCUMENT_BYTES = 1024 * 1024
ROLES = {"requirement", "supporting", "unspecified"}
STATUSES = {"found", "missing", "invalid_path", "not_file", "too_large", "unreadable", "external_unobserved", "unsupported_reference", "ambiguous_path"}


def _context_doc_lines(raw: str) -> list[str]:
    """Exclude fenced examples and HTML comments before parsing active references."""
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    lines: list[str] = []
    fence_char, fence_length = "", 0
    for line in raw.splitlines():
        fence = re.match(r"^\s{0,3}(" + r"\x60{3,}|~{3,}" + r")", line)
        if fence:
            marker = fence.group(1)
            if not fence_char:
                fence_char, fence_length = marker[0], len(marker)
                continue
            if marker[0] == fence_char and len(marker) >= fence_length:
                fence_char, fence_length = "", 0
                continue
        if not fence_char:
            lines.append(line)
    return lines


def _reference_target(
    target: str, *, task_path: str, markdown_link: bool,
) -> dict[str, str]:
    target = unquote(target.strip().strip("<>"))
    path, _, fragment = target.partition("#")
    section = fragment[:128]
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", path):
        return {"path": path, "section": section, "_kind": "external"}
    if not path or "?" in path or "\\" in path or "\x00" in path:
        return {"path": target or "(empty reference)", "section": section, "_kind": "unsupported"}
    if path.startswith("/"):
        normalized = posixpath.normpath(path.lstrip("/"))
    elif path.startswith(("docs/", "repos/")) or path in {"AGENTS.md", "README.md", "CLAUDE.md"}:
        normalized = path
    elif markdown_link or path.startswith(("./", "../")):
        normalized = posixpath.normpath(posixpath.join(posixpath.dirname(task_path), path)) if task_path else path
    else:
        return {"path": path, "section": section, "_kind": "unsupported"}
    result = {"path": normalized, "section": section}
    if markdown_link and task_path and path.startswith(("docs/", "repos/")):
        alternate = posixpath.normpath(posixpath.join(posixpath.dirname(task_path), path))
        if alternate != normalized:
            result["_alternate"] = alternate
    return result


def context_doc_references(body: str, *, task_path: str = "") -> list[dict[str, str]]:
    try:
        section = find_section(body, "Context Docs")
    except RepoctlError:
        return []
    lines = _context_doc_lines(body[section.body_start:section.end])
    definitions = {
        match.group(1).strip().casefold(): match.group(2)
        for line in lines
        if (match := re.match(r"^\s{0,3}\[([^\]]+)\]:\s*<?([^>\s]+)>?", line))
    }
    references: list[dict[str, str]] = []
    positions: dict[tuple[str, str], int] = {}
    for line in lines:
        item = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$", line)
        if not item:
            continue
        value = item.group(1).strip()
        role_match = re.match(r"^(requirement|supporting)\s*:\s*", value, re.IGNORECASE)
        role = role_match.group(1).lower() if role_match else "unspecified"
        if role_match:
            value = value[role_match.end():].strip()
        candidates: list[tuple[str, bool]] = []
        for match in re.finditer(r"\[[^\]]+\]\(\s*(<[^>]*>|[^\s)]+)", value):
            candidates.append((match.group(1), True))
        for match in re.finditer(r"\[([^\]]+)\]\[([^\]]*)\]", value):
            label = (match.group(2) or match.group(1)).strip().casefold()
            if label in definitions:
                candidates.append((definitions[label], True))
            else:
                candidates.append((match.group(0), False))
        if not candidates:
            shortcut = re.fullmatch(r"\[([^\]]+)\]", value)
            if shortcut and shortcut.group(1).casefold() in definitions:
                candidates.append((definitions[shortcut.group(1).casefold()], True))
        for match in re.finditer(r"\x60([^\x60]+)\x60", value):
            candidates.append((match.group(1), False))
        if not candidates:
            bare = value.strip().rstrip(".,;:")
            if bare.lower() in {"none", "n/a", "no context docs", "no context documents"}:
                continue
            if re.fullmatch(r"(?:https?://\S+|/?(?:docs|repos)/\S+|(?:\.\./|\./)\S+|(?:AGENTS|README|CLAUDE)\.md(?:#\S+)?|[^\s]+\.(?:md|txt|json)(?:#\S+)?)", bare, re.I):
                candidates.append((bare, False))
            elif role_match or value.startswith("["):
                candidates.append((bare or "(empty reference)", False))
        for target, is_markdown in candidates:
            ref = _reference_target(target, task_path=task_path, markdown_link=is_markdown)
            key = ref["path"], ref["section"]
            existing = positions.get(key)
            if existing is not None:
                if role == "requirement":
                    references[existing]["role"] = role
                elif role == "supporting" and references[existing]["role"] == "unspecified":
                    references[existing]["role"] = role
                continue
            positions[key] = len(references)
            references.append({**ref, "role": role})
    return references


def context_doc_paths(body: str, *, task_path: str = "") -> list[str]:
    return [item["path"] for item in context_doc_references(body, task_path=task_path) if item.get("_kind") not in {"external", "unsupported"}]


def _safe_document(root: Path, relative: str) -> tuple[Path | None, str]:
    rel = PurePosixPath(relative)
    if (
        not relative or relative != rel.as_posix() or rel.is_absolute()
        or ".." in rel.parts or "\\" in relative or "\x00" in relative
    ):
        return None, "invalid_path"
    path = root.joinpath(*rel.parts)
    try:
        path.relative_to(root)
        current = root
        for part in rel.parts:
            current /= part
            if current.is_symlink():
                return None, "invalid_path"
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, ValueError, RuntimeError):
        return None, "invalid_path"
    return path, ""


def observe_context_docs(root: Path, body: str, *, task_path: str = "") -> dict[str, Any]:
    references = context_doc_references(body, task_path=task_path)
    observations: list[dict[str, str]] = []
    for ref in references[:MAX_REFERENCES]:
        kind = ref.get("_kind", "")
        if kind:
            status = "external_unobserved" if kind == "external" else "unsupported_reference"
            content_digest = ""
        else:
            path, invalid = _safe_document(root, ref["path"])
            status, content_digest = invalid, ""
            alternate = ref.get("_alternate", "")
            if alternate and path is not None:
                alternative, alt_problem = _safe_document(root, alternate)
                if not alt_problem and alternative is not None and path.is_file() and alternative.is_file():
                    status = "ambiguous_path"
            if status != "ambiguous_path" and path is not None:
                if not path.exists():
                    status = "missing"
                elif not path.is_file():
                    status = "not_file"
                else:
                    try:
                        if path.stat().st_size > MAX_DOCUMENT_BYTES:
                            status = "too_large"
                        else:
                            with path.open("rb") as source:
                                content = source.read(MAX_DOCUMENT_BYTES + 1)
                            if len(content) > MAX_DOCUMENT_BYTES:
                                status = "too_large"
                            else:
                                status = "found"
                                content_digest = "sha256:" + hashlib.sha256(content).hexdigest()
                    except OSError:
                        status = "unreadable"
        observations.append({
            "path": ref["path"],
            "section": ref["section"],
            "role": ref["role"],
            "status": status,
            "content_sha256": content_digest,
        })
    return {
        "documents": observations,
        "reference_count": len(references),
        "truncated": len(references) > MAX_REFERENCES,
    }


def context_doc_coverage(observation: dict[str, Any]) -> dict[str, Any]:
    """Public, bounded observation coverage, independent of binding freshness."""
    documents = observation["documents"]
    issues = [item for item in documents if item["status"] != "found"]
    unobserved_count = observation["reference_count"] - len(documents)
    status_counts = {
        status: sum(item["status"] == status for item in documents)
        for status in sorted({item["status"] for item in issues})
    }
    return {
        "reference_count": observation["reference_count"],
        "observed_count": len(documents),
        "unobserved_count": unobserved_count,
        "truncated": observation["truncated"],
        "incomplete": bool(issues or unobserved_count),
        "required_unobserved": bool(
            unobserved_count or any(item["role"] == "requirement" for item in issues)
        ),
        "status_counts": status_counts,
        "issues": [
            {"path": item["path"], "role": item["role"], "status": item["status"]}
            for item in issues[:8]
        ],
        "issues_truncated": len(issues) > 8,
    }


def validate_context_doc_observation(data: Any) -> bool:
    if not isinstance(data, dict) or set(data) != {"documents", "reference_count", "truncated"}:
        return False
    documents = data["documents"]
    if (
        not isinstance(documents, list) or len(documents) > MAX_REFERENCES
        or type(data["reference_count"]) is not int
        or data["reference_count"] < len(documents)
        or type(data["truncated"]) is not bool
        or data["truncated"] != (data["reference_count"] > MAX_REFERENCES)
    ):
        return False
    for item in documents:
        if not isinstance(item, dict) or set(item) != {"path", "section", "role", "status", "content_sha256"}:
            return False
        if (
            not isinstance(item["path"], str) or not item["path"]
            or not isinstance(item["section"], str) or len(item["section"]) > 128
            or item["role"] not in ROLES or item["status"] not in STATUSES
        ):
            return False
        digest = item["content_sha256"]
        if not isinstance(digest, str) or (
            bool(re.fullmatch(r"sha256:[0-9a-f]{64}", digest)) != (item["status"] == "found")
        ):
            return False
    return True


def context_doc_changes(bound: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    before = {(item["path"], item["section"]): item for item in bound["documents"]}
    after = {(item["path"], item["section"]): item for item in current["documents"]}
    changes: list[dict[str, Any]] = []
    for key in sorted(before.keys() | after.keys()):
        original, observed = before.get(key), after.get(key)
        if original == observed:
            continue
        role = (observed or original)["role"]
        if original is None:
            change = "added"
        elif observed is None:
            change = "removed"
        elif original["status"] == "found" and observed["status"] == "missing":
            change = "missing"
        elif original["status"] == "missing" and observed["status"] == "found":
            change = "restored"
        elif original["status"] == observed["status"] == "found" and original["content_sha256"] != observed["content_sha256"]:
            change = "changed"
        else:
            change = "status_or_role_changed"
        changes.append({
            "path": key[0],
            "section": key[1],
            "change": change,
            "role": role,
            "review_severity": "review_required" if role == "requirement" else "advisory" if role == "supporting" else "unclassified",
            "before_status": original["status"] if original else "not_referenced",
            "after_status": observed["status"] if observed else "not_referenced",
            "before_digest": original["content_sha256"] if original else "",
            "after_digest": observed["content_sha256"] if observed else "",
        })
    if bound["truncated"] != current["truncated"] or bound["reference_count"] != current["reference_count"]:
        changes.append({
            "path": "Context Docs",
            "change": "reference_count_changed",
            "role": "unspecified",
            "review_severity": "unclassified",
            "before_count": bound["reference_count"],
            "after_count": current["reference_count"],
            "truncated": current["truncated"],
        })
    return changes
