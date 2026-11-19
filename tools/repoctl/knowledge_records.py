from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from uuid import uuid4

from .io import Problem, atomic_write
from .repositories import (
    require_repo_target,
    resolve_repo_selector_path,
    RepoSelectorStatus,
)


ALLOWED_KINDS = {"decision", "invariant", "failure_mode"}


class KnowledgeExplicitPathKind(StrEnum):
    APPLIES_TO_PATH = "applies_to_path"
    SOURCE_REF = "source_ref"


class KnowledgeExplicitPathRole(StrEnum):
    CODE_ANCHOR = "code_anchor"
    PROVENANCE_ONLY = "provenance_only"


class KnowledgeSourceRefKind(StrEnum):
    CURRENT_SOURCE = "current_source"
    AUTHORITY_DOCUMENT = "authority_document"
    TASK_ARTIFACT = "task_artifact"
    WORKSPACE_FILE = "workspace_file"


class KnowledgeQueryMatchStrength(StrEnum):
    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"
    EXACT = "exact"


@dataclass(frozen=True)
class PreparedKnowledgeRecord:
    data: dict[str, Any]
    path: Path
    text: str


def _record_dir(root: Path) -> Path:
    return root / "docs/knowledge/records"


def _workspace_path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or ".." in relative.parts or "\\" in value:
        raise ValueError("source must be a workspace-relative path")
    path = root / relative
    path.resolve().relative_to(root.resolve())
    return path


def _read_records(
    root: Path, *, repo_id: str
) -> tuple[list[dict[str, Any]], list[Problem]]:
    records: list[dict[str, Any]] = []
    problems: list[Problem] = []
    for path in sorted(_record_dir(root).glob("*.json")):
        relative = path.relative_to(root).as_posix()
        try:
            path.resolve().relative_to(root.resolve())
            content = path.read_bytes()
            record = json.loads(content)
            if not isinstance(record, dict):
                raise ValueError("record must be an object")
            if str(record.get("repo_id") or "") != repo_id:
                continue
            if record.get("id") != path.stem:
                raise ValueError("record ID does not match its filename")
            if not isinstance(record.get("claim"), str) or not record["claim"].strip():
                raise ValueError("record has no claim")
            refs = record.get("source_refs")
            if (
                not isinstance(refs, list)
                or not refs
                or any(
                    not isinstance(ref, dict)
                    or not isinstance(ref.get("path"), str)
                    or not ref["path"]
                    for ref in refs
                )
            ):
                raise ValueError("record source_refs must contain source paths")
            applies = record.get("applies_to", {})
            if not isinstance(applies, dict) or not isinstance(
                applies.get("paths", []), list
            ):
                raise ValueError("record applies_to.paths must be a list")
            if not isinstance(record.get("replaces", []), list):
                raise ValueError("record replaces must be a list")
            record["_content_sha256"] = "sha256:" + hashlib.sha256(content).hexdigest()
            records.append(record)
        except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
            problems.append(
                Problem("error", "knowledge_record_unreadable", str(exc), relative)
            )
    return records, problems


def _superseded_ids(records: list[dict[str, Any]]) -> set[str]:
    present = {str(record["id"]) for record in records}
    return {
        str(value)
        for record in records
        for value in record.get("replaces", [])
        if str(value) in present
    }


def _source_kind(value: str, repository_path: str) -> str:
    value = PurePosixPath(value).as_posix()
    if value.startswith(("docs/tasks/", "docs/archive/tasks/")) and value.endswith(
        ".md"
    ):
        return KnowledgeSourceRefKind.TASK_ARTIFACT.value
    if value.startswith(
        (
            "docs/adr/",
            "docs/architecture/",
            "docs/contracts/",
            "docs/specs/",
            "docs/workflows/",
        )
    ):
        return KnowledgeSourceRefKind.AUTHORITY_DOCUMENT.value
    if repository_path == ".":
        if not value.startswith("docs/") and value not in {"AGENTS.md", "README.md"}:
            return KnowledgeSourceRefKind.CURRENT_SOURCE.value
    elif repository_path and value.startswith(repository_path.rstrip("/") + "/"):
        return KnowledgeSourceRefKind.CURRENT_SOURCE.value
    return KnowledgeSourceRefKind.WORKSPACE_FILE.value


def prepare_knowledge_record(
    root: Path,
    *,
    repo_id: str,
    kind: str,
    claim: str,
    reason: str,
    sources: list[str],
    applies_to: list[str] | None = None,
    replaces: list[str] | None = None,
    author: str = "",
    source_bytes: Mapping[str, bytes] | None = None,
) -> tuple[PreparedKnowledgeRecord | None, list[Problem]]:
    """Prepare a record; supplied source bytes are the exact bytes its caller will save."""
    problems: list[Problem] = []
    if kind not in ALLOWED_KINDS:
        problems.append(
            Problem(
                "error", "invalid_knowledge_kind", f"unknown Knowledge kind: {kind}"
            )
        )
    if not claim.strip():
        problems.append(
            Problem("error", "knowledge_claim_required", "Knowledge requires a claim")
        )
    if not reason.strip():
        problems.append(
            Problem("error", "knowledge_reason_required", "Knowledge requires a reason")
        )
    if not sources:
        problems.append(
            Problem(
                "error",
                "knowledge_source_required",
                "Knowledge requires at least one source",
            )
        )
    target = require_repo_target(root, repo_id=repo_id) if repo_id else None
    repository_path = target.display_path if target else ""
    applicability: list[str] = []
    for value in applies_to or []:
        resolution = resolve_repo_selector_path(value, repository_path=repository_path)
        if resolution.status != RepoSelectorStatus.RESOLVED:
            problems.append(
                Problem(
                    "error",
                    "knowledge_path_invalid",
                    "invalid applicability path",
                    value,
                )
            )
        elif resolution.path not in applicability:
            applicability.append(resolution.path)
    refs: list[dict[str, str]] = []
    for value in dict.fromkeys(sources):
        try:
            path = _workspace_path(root, value)
            content = (
                source_bytes[value]
                if source_bytes is not None and value in source_bytes
                else path.read_bytes()
            )
            if not isinstance(content, bytes):
                raise ValueError("source_bytes values must be bytes")
            refs.append(
                {
                    "kind": _source_kind(value, repository_path),
                    "path": value,
                    "content_sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                }
            )
        except (OSError, ValueError, RuntimeError) as exc:
            problems.append(
                Problem("error", "knowledge_source_unavailable", str(exc), value)
            )
    replacement_ids = sorted(set(replaces or []))
    if replacement_ids:
        existing, read_problems = _read_records(root, repo_id=repo_id)
        problems.extend(read_problems)
        known = {str(record["id"]) for record in existing}
        for record_id in replacement_ids:
            if record_id not in known:
                problems.append(
                    Problem(
                        "error",
                        "knowledge_replaces_missing",
                        "replacement target is not in this repository",
                        record_id,
                    )
                )
    if problems:
        return None, problems
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%SZ")
    slug = (
        re.sub(r"[^a-z0-9]+", "-", claim.casefold()).strip("-")[:40].strip("-")
        or "knowledge"
    )
    record_id = f"K-{stamp}--{slug}-{uuid4().hex[:12]}"
    record: dict[str, Any] = {
        "schema": "repoctl.knowledge.record",
        "schema_version": 2,
        "id": record_id,
        "repo_id": repo_id,
        "kind": kind,
        "claim": claim.strip(),
        "reason": reason.strip(),
        "source_refs": refs,
        "applies_to": {"paths": sorted(applicability)},
        "replaces": replacement_ids,
        "recorded_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }
    if author.strip():
        record["author"] = author.strip()
    return PreparedKnowledgeRecord(
        record,
        _record_dir(root) / f"{record_id}.json",
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    ), []


def add_knowledge_record(
    root: Path,
    *,
    repo_id: str,
    kind: str,
    claim: str,
    reason: str,
    sources: list[str],
    applies_to: list[str] | None = None,
    replaces: list[str] | None = None,
    author: str = "",
) -> tuple[dict[str, Any], list[Problem], list[Problem]]:
    prepared, problems = prepare_knowledge_record(
        root,
        repo_id=repo_id,
        kind=kind,
        claim=claim,
        reason=reason,
        sources=sources,
        applies_to=applies_to,
        replaces=replaces,
        author=author,
    )
    if prepared is None:
        return {}, problems, []
    atomic_write(prepared.path, prepared.text)
    return (
        {
            "record": prepared.data,
            "record_path": prepared.path.relative_to(root).as_posix(),
        },
        [],
        [],
    )


def _observe_sources(
    root: Path, record: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolutions: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for source in record.get("source_refs", []):
        if not isinstance(source, dict):
            continue
        value = str(source.get("path") or "")
        expected = str(source.get("content_sha256") or "")
        actual = ""
        try:
            content = _workspace_path(root, value).read_bytes()
            actual = "sha256:" + hashlib.sha256(content).hexdigest()
            status = (
                "current"
                if expected.removeprefix("sha256:") == actual.removeprefix("sha256:")
                else "digest_mismatch"
                if expected
                else "unobserved"
            )
        except (OSError, ValueError, RuntimeError):
            status = "missing"
        resolution = {
            "path": value,
            "declared_path": value,
            "resolved_path": value,
            "kind": str(source.get("kind") or "workspace_file"),
            "status": status,
            "expected_sha256": expected,
            "actual_sha256": actual,
            "exists": bool(actual),
            "digest_matches": bool(expected)
            and expected.removeprefix("sha256:") == actual.removeprefix("sha256:"),
        }
        resolutions.append(resolution)
        refs.append(
            {
                **source,
                "declared_path": value,
                "resolved_path": value,
                "resolution_status": status,
            }
        )
    return resolutions, refs


def explicit_knowledge_path_refs(record: dict[str, Any]) -> list[dict[str, Any]]:
    applies = (
        record.get("applies_to") if isinstance(record.get("applies_to"), dict) else {}
    )
    refs = [
        {
            "kind": KnowledgeExplicitPathKind.APPLIES_TO_PATH.value,
            "role": KnowledgeExplicitPathRole.CODE_ANCHOR.value,
            "path": path,
        }
        for path in applies.get("paths", [])
        if isinstance(path, str)
    ]
    for source in record.get("source_refs", []):
        if isinstance(source, dict) and source.get("path"):
            refs.append(
                {
                    "kind": KnowledgeExplicitPathKind.SOURCE_REF.value,
                    "role": (
                        KnowledgeExplicitPathRole.CODE_ANCHOR.value
                        if source.get("kind")
                        == KnowledgeSourceRefKind.CURRENT_SOURCE.value
                        else KnowledgeExplicitPathRole.PROVENANCE_ONLY.value
                    ),
                    "path": str(source["path"]),
                    "source_kind": str(source.get("kind") or "workspace_file"),
                }
            )
    return refs


def _public_record(
    root: Path, record: dict[str, Any], *, status: str, observe: bool
) -> dict[str, Any]:
    resolutions, refs = (
        _observe_sources(root, record)
        if observe
        else (
            [],
            [
                {
                    **source,
                    "declared_path": source.get("path", ""),
                    "resolved_path": source.get("path", ""),
                    "resolution_status": "unobserved",
                }
                for source in record.get("source_refs", [])
                if isinstance(source, dict)
            ],
        )
    )
    return {
        **{key: value for key, value in record.items() if not key.startswith("_")},
        "content_sha256": record.get("_content_sha256", ""),
        "status": status,
        "title": record.get("title") or record["claim"],
        "summary": record.get("summary") or record.get("reason", ""),
        "explicit_path_refs": explicit_knowledge_path_refs(record),
        "resolved_source_refs": refs,
        "source_resolutions": resolutions,
    }


def knowledge_status(root: Path, *, repo_id: str) -> dict[str, Any]:
    records, problems = _read_records(root, repo_id=repo_id)
    superseded = _superseded_ids(records)
    return {
        "schema": "repoctl.knowledge.status",
        "schema_version": 2,
        "repo_id": repo_id,
        "record_count": len(records),
        "record_statuses": {
            "current": len(records) - len(superseded),
            "superseded": len(superseded),
        },
        "problems": [problem.to_dict() for problem in problems],
    }


def show_knowledge_record(
    root: Path, *, record_id: str, repo_id: str
) -> tuple[dict[str, Any], list[Problem]]:
    records, problems = _read_records(root, repo_id=repo_id)
    superseded = _superseded_ids(records)
    for record in records:
        if record["id"] == record_id:
            return {
                "record": _public_record(
                    root,
                    record,
                    status="superseded" if record_id in superseded else "current",
                    observe=True,
                ),
                "path": (_record_dir(root) / f"{record_id}.json")
                .relative_to(root)
                .as_posix(),
            }, problems
    return {}, [
        *problems,
        Problem(
            "error",
            "knowledge_record_not_found",
            "Knowledge record not found",
            record_id,
        ),
    ]


def check_knowledge_records(
    root: Path, *, repo_id: str
) -> tuple[dict[str, Any], list[Problem]]:
    records, problems = _read_records(root, repo_id=repo_id)
    superseded = _superseded_ids(records)
    results: list[dict[str, Any]] = []
    for record in records:
        item = _public_record(
            root,
            record,
            status="superseded" if record["id"] in superseded else "current",
            observe=True,
        )
        results.append(item)
        problems.extend(
            Problem(
                "warning",
                "knowledge_source_changed",
                "saved source bytes differ or are unavailable",
                str(ref["path"]),
                cause_code=str(ref["status"]),
            )
            for ref in item["source_resolutions"]
            if ref["status"] != "current"
        )
    return {
        "repo_id": repo_id,
        "record_count": len(records),
        "records": results,
    }, problems


def query_knowledge_records(
    root: Path,
    *,
    repo_id: str,
    query: str,
    include_history: bool = False,
    limit: int = 10,
    explain: bool = False,
    related_paths: set[str] | None = None,
    require_related: bool = False,
) -> tuple[dict[str, Any], list[Problem], list[Problem]]:
    records, problems = _read_records(root, repo_id=repo_id)
    superseded = _superseded_ids(records)
    terms = tuple(dict.fromkeys(re.findall(r"[\w./-]+", query.casefold())))
    wanted = related_paths or set()
    ranked: list[tuple[float, dict[str, Any], list[str], str, list[str]]] = []
    for record in records:
        if not include_history and record["id"] in superseded:
            continue
        paths = {str(ref["path"]) for ref in explicit_knowledge_path_refs(record)}
        matched_paths = sorted(wanted & paths)
        if require_related and not matched_paths:
            continue
        fields = [
            str(record.get(key) or "").casefold()
            for key in ("id", "title", "claim", "reason", "summary")
        ]
        body = "\n".join([*fields, *sorted(paths)]).casefold()
        matched_terms = [term for term in terms if term in body]
        exact = bool(query.strip()) and any(
            query.strip().casefold() == field for field in fields[:3]
        )
        claim_text = "\n".join(fields[1:3])
        if terms and not matched_terms and not matched_paths:
            continue
        score = (
            (10.0 if exact else 0.0)
            + len(matched_terms)
            + (2.0 if matched_paths else 0.0)
        )
        strength = (
            "exact"
            if exact
            else "strong"
            if terms and all(term in claim_text for term in terms)
            else "weak"
            if matched_terms
            else "none"
        )
        ranked.append((score, record, matched_paths, strength, matched_terms))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["id"])))
    results: list[dict[str, Any]] = []
    warnings: list[Problem] = []
    for score, record, paths, strength, matched in ranked[: max(0, limit)]:
        public = _public_record(
            root,
            record,
            status="superseded" if record["id"] in superseded else "current",
            observe=True,
        )
        for ref in public["source_resolutions"]:
            if ref["status"] != "current":
                warnings.append(
                    Problem(
                        "warning",
                        "knowledge_source_changed",
                        "saved source bytes differ or are unavailable",
                        str(ref["path"]),
                        cause_code=str(ref["status"]),
                    )
                )
        item = {
            "record": public,
            "score": score,
            "score_breakdown": {
                "exact_identity": 10.0 if strength == "exact" else 0.0,
                "lexical": float(len(matched)),
                "path_relation": 2.0 if paths else 0.0,
            },
            "selection_reasons": [
                *(f"matched term: {term}" for term in matched),
                *(f"explicit path: {path}" for path in paths),
            ],
            "query_match_strength": strength,
            "matched_paths": paths,
        }
        if explain:
            item["explain"] = {"source_ref_statuses": public["source_resolutions"]}
        results.append(item)
    return (
        {
            "schema": "repoctl.knowledge.query",
            "schema_version": 2,
            "repo_id": repo_id,
            "query": {"text": query, "include_history": include_history},
            "results": results,
            "available_record_count": len(records),
            "result_count": len(results),
            "lifecycle": {
                "current": len(records) - len(superseded),
                "superseded": len(superseded),
            },
            "summary": {
                "record_count": len(records),
                "matched_count": len(ranked),
                "returned_count": len(results),
                "source_observed_count": len(results),
                "truncated": len(ranked) > len(results),
            },
        },
        problems,
        warnings,
    )


def knowledge_records_for_graph(
    root: Path, *, repo_id: str
) -> tuple[list[dict[str, Any]], list[Problem]]:
    records, problems = _read_records(root, repo_id=repo_id)
    superseded = _superseded_ids(records)
    return [
        _public_record(root, record, status="current", observe=False)
        for record in records
        if record["id"] not in superseded
    ], problems
