from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .io import Problem, atomic_write
from .repositories import (
    resolve_repo_selector_path,
    RepoSelectorStatus,
)


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
    root: Path, *, repo_id: str, record_ids: set[str] | None = None
) -> tuple[list[dict[str, Any]], list[Problem]]:
    records: list[dict[str, Any]] = []
    problems: list[Problem] = []
    for path in sorted(_record_dir(root).glob("*.json")):
        if record_ids is not None and path.stem not in record_ids:
            continue
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
            for field in ("kind", "reason", "author"):
                if field in record and not isinstance(record[field], str):
                    raise ValueError(f"record {field} must be text")
            refs = record.get("source_refs", [])
            if not isinstance(refs, list) or any(
                not isinstance(ref, dict)
                or not isinstance(ref.get("path"), str)
                or not ref["path"]
                or not isinstance(ref.get("content_sha256"), str)
                or not ref["content_sha256"]
                for ref in refs
            ):
                raise ValueError("record source_refs must contain paths and content hashes")
            applies = record.get("applies_to", {})
            if (
                not isinstance(applies, dict)
                or not isinstance(applies.get("paths", []), list)
                or any(not isinstance(value, str) for value in applies.get("paths", []))
            ):
                raise ValueError("record applies_to.paths must be a list of paths")
            replaces = record.get("replaces", [])
            if not isinstance(replaces, list) or any(
                not isinstance(value, str) for value in replaces
            ):
                raise ValueError("record replaces must be a list of IDs")
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


def add_knowledge_record(
    root: Path,
    *,
    repo_id: str,
    claim: str,
    kind: str = "",
    reason: str = "",
    sources: list[str] | None = None,
    applies_to: list[str] | None = None,
    replaces: list[str] | None = None,
    author: str = "",
) -> tuple[dict[str, Any], list[Problem], list[Problem]]:
    """Save an explicit claim and any references supplied by its author."""
    problems: list[Problem] = []
    if not claim.strip():
        problems.append(
            Problem("error", "knowledge_claim_required", "Knowledge requires a claim")
        )
    applicability: list[str] = []
    for value in applies_to or []:
        resolution = resolve_repo_selector_path(value)
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
    seen_sources: set[str] = set()
    for value in sources or []:
        try:
            path = _workspace_path(root, value)
            relative = path.relative_to(root).as_posix()
            if relative in seen_sources:
                continue
            seen_sources.add(relative)
            content = path.read_bytes()
            refs.append(
                {
                    "path": relative,
                    "content_sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                }
            )
        except (OSError, ValueError, RuntimeError) as exc:
            problems.append(
                Problem("error", "knowledge_source_unavailable", str(exc), value)
            )
    replacement_ids = sorted(set(replaces or []))
    if replacement_ids:
        existing, read_problems = _read_records(root, repo_id=repo_id, record_ids=set(replacement_ids))
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
        return {}, problems, []
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%SZ")
    record_id = f"K-{stamp}-{uuid4().hex[:12]}"
    record: dict[str, Any] = {
        "id": record_id,
        "repo_id": repo_id,
        "claim": claim.strip(),
        "recorded_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }
    for field, value in (("kind", kind), ("reason", reason), ("author", author)):
        if value.strip():
            record[field] = value.strip()
    if refs:
        record["source_refs"] = refs
    if applicability:
        record["applies_to"] = {"paths": sorted(applicability)}
    if replacement_ids:
        record["replaces"] = replacement_ids
    path = _record_dir(root) / f"{record_id}.json"
    atomic_write(path, json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"record": record, "record_path": path.relative_to(root).as_posix()}, [], []


def _source_statuses(root: Path, record: dict[str, Any]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for source in record.get("source_refs", []):
        value = source["path"]
        recorded = source["content_sha256"]
        current = ""
        error = ""
        try:
            content = _workspace_path(root, value).read_bytes()
            current = "sha256:" + hashlib.sha256(content).hexdigest()
            status = "unchanged" if current == recorded else "changed"
        except FileNotFoundError:
            status = "missing"
        except (OSError, ValueError, RuntimeError) as exc:
            status = "unavailable"
            error = str(exc)
        item = {
            "path": value,
            "status": status,
            "recorded_sha256": recorded,
            "current_sha256": current,
        }
        if error:
            item["error"] = error
        results.append(item)
    return results


def _public_record(
    root: Path, record: dict[str, Any], *, status: str, observe_sources: bool
) -> dict[str, Any]:
    result = {
        **{key: value for key, value in record.items() if not key.startswith("_")},
        "content_sha256": record["_content_sha256"],
        "status": status,
    }
    if observe_sources:
        result["source_statuses"] = _source_statuses(root, record)
    return result


def _source_problems(record: dict[str, Any]) -> list[Problem]:
    messages = {
        "changed": "source bytes differ from the saved reference",
        "missing": "source file is missing",
        "unavailable": "source cannot be read",
    }
    return [
        Problem(
            "warning", "knowledge_source_" + source["status"],
            messages[source["status"]], source["path"],
        )
        for source in record["source_statuses"]
        if source["status"] != "unchanged"
    ]


def knowledge_status(root: Path, *, repo_id: str) -> dict[str, Any]:
    records, problems = _read_records(root, repo_id=repo_id)
    superseded = _superseded_ids(records)
    return {
        "repo_id": repo_id,
        "record_count": len(records),
        "record_statuses": {
            "active": len(records) - len(superseded),
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
                    status="superseded" if record_id in superseded else "active",
                    observe_sources=True,
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
            status="superseded" if record["id"] in superseded else "active",
            observe_sources=True,
        )
        results.append(item)
        problems.extend(_source_problems(item))
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
    include_superseded: bool = False,
    limit: int = 10,
) -> tuple[dict[str, Any], list[Problem], list[Problem]]:
    records, problems = _read_records(root, repo_id=repo_id)
    superseded = _superseded_ids(records)
    terms = tuple(dict.fromkeys(re.findall(r"[\w./-]+", query.casefold())))
    ranked: list[tuple[bool, list[str], dict[str, Any]]] = []
    for record in records:
        if not include_superseded and record["id"] in superseded:
            continue
        fields = [
            str(record.get(key) or "").casefold()
            for key in ("id", "claim", "reason", "kind")
        ]
        paths = [
            *record.get("applies_to", {}).get("paths", []),
            *(ref["path"] for ref in record.get("source_refs", [])),
        ]
        text = "\n".join([*fields, *paths]).casefold()
        matched_terms = [term for term in terms if term in text]
        exact_match = bool(query.strip()) and query.strip().casefold() in fields[:2]
        if terms and not matched_terms:
            continue
        ranked.append((exact_match, matched_terms, record))
    ranked.sort(key=lambda item: (not item[0], -len(item[1]), item[2]["id"]))
    results: list[dict[str, Any]] = []
    warnings: list[Problem] = []
    selected = ranked if limit < 0 else ranked[:limit]
    for exact_match, matched_terms, record in selected:
        public = _public_record(
            root,
            record,
            status="superseded" if record["id"] in superseded else "active",
            observe_sources=True,
        )
        warnings.extend(_source_problems(public))
        results.append({
            "record": public,
            "exact_match": exact_match,
            "matched_terms": matched_terms,
        })
    return {
        "repo_id": repo_id,
        "query": {"text": query, "include_superseded": include_superseded},
        "results": results,
        "record_count": len(records),
        "matched_count": len(ranked),
        "result_count": len(results),
        "truncated": len(ranked) > len(results),
    }, problems, warnings


def knowledge_records_for_graph(
    root: Path, *, repo_id: str
) -> tuple[list[dict[str, Any]], list[Problem]]:
    records, problems = _read_records(root, repo_id=repo_id)
    superseded = _superseded_ids(records)
    return [
        _public_record(root, record, status="active", observe_sources=False)
        for record in records
        if record["id"] not in superseded
    ], problems
