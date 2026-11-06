"""Bounded, source-linked observation of explicitly referenced Task Context Docs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .io import RepoctlError
from .markdown import find_section


MAX_REFERENCES = 32
MAX_DOCUMENT_BYTES = 1024 * 1024
ROLES = {"requirement", "supporting", "unspecified"}
STATUSES = {"found", "missing", "invalid_path", "not_file", "too_large", "unreadable"}


def context_doc_references(body: str) -> list[dict[str, str]]:
    try:
        section = find_section(body, "Context Docs")
    except RepoctlError:
        return []
    raw = body[section.body_start:section.end]
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    references: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or not stripped.startswith("-"):
            continue
        explicit_role = re.match(r"^-\s*(requirement|supporting)\s*:", stripped, re.IGNORECASE)
        role = explicit_role.group(1).lower() if explicit_role else "unspecified"
        candidates = re.findall(r"`([^`]+)`", stripped)
        if not candidates:
            raw_candidate = stripped[1:].strip()
            if explicit_role:
                raw_candidate = raw_candidate.split(":", 1)[1].strip()
            candidates = [raw_candidate]
        for candidate in candidates:
            candidate = candidate.strip().strip(".,;:")
            path, _separator, section_ref = candidate.partition("#")
            if not (path.startswith(("docs/", "repos/")) or path in {"AGENTS.md", "README.md", "CLAUDE.md"}):
                continue
            key = (path, section_ref)
            if key not in seen:
                references.append({
                    "path": path,
                    "section": section_ref[:128],
                    "role": role,
                })
                seen.add(key)
    return references


def context_doc_paths(body: str) -> list[str]:
    return [item["path"] for item in context_doc_references(body)]


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


def observe_context_docs(root: Path, body: str) -> dict[str, Any]:
    references = context_doc_references(body)
    observations: list[dict[str, str]] = []
    for ref in references[:MAX_REFERENCES]:
        path, invalid = _safe_document(root, ref["path"])
        status, content_digest = invalid, ""
        if path is not None:
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
            **ref,
            "status": status,
            "content_sha256": content_digest,
        })
    return {
        "documents": observations,
        "reference_count": len(references),
        "truncated": len(references) > MAX_REFERENCES,
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
