from __future__ import annotations

import math
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .io import Problem
from .repositories import RepoTarget
from .source_index import (
    SourceText, collect_source_files, file_signature, open_source_index,
    read_source, source_index_path, tokenize,
)


def _path_match(path: str, query: str, target: RepoTarget) -> str:
    prefix = target.display_path.rstrip("/") + "/"
    relative = path.removeprefix(prefix) if target.display_path != "." else path
    if query in {path, relative}:
        return "exact_path"
    if query == Path(path).name:
        return "exact_filename"
    return "terms"


def _snippet(text: str, terms: set[str], query: str) -> tuple[int, int, str]:
    lines = text.splitlines()
    if not lines:
        return 1, 1, ""
    matches = [
        (int(query.casefold() in line.casefold()), len(terms.intersection(tokenize(line))), -index)
        for index, line in enumerate(lines)
    ]
    best = -max(matches)[2]
    start, end = max(0, best - 2), min(len(lines), best + 3)
    return start + 1, end, "\n".join(lines[start:end])


def _search(
    root: Path, *, target: RepoTarget, query: str, limit: int,
    connection: sqlite3.Connection | None,
) -> tuple[dict[str, Any], list[Problem]]:
    files, skipped, problems = collect_source_files(root, target=target)
    terms = set(tokenize(query))
    cached = {
        row["path"]: dict(row)
        for row in connection.execute("SELECT path, kind, signature, token_count, content_sha256 FROM documents")
    } if connection is not None else {}
    changed: set[str] = set()
    for path, source in files.items():
        try:
            signature = file_signature(source.absolute_path)
        except OSError:
            signature = ""
        if (
            path not in cached or cached[path]["signature"] != signature
            or cached[path]["kind"] != source.kind
        ):
            changed.add(path)
    deleted = set(cached) - set(files)
    replaced = changed | deleted
    cached_paths = set(cached) - replaced
    count = len(cached_paths)
    total_length = sum(cached[path]["token_count"] for path in cached_paths)
    document_frequency: Counter[str] = Counter()
    frequencies: dict[str, Counter[str]] = {}
    if connection is not None:
        for term in sorted(terms):
            for row in connection.execute("SELECT path, frequency FROM terms WHERE term=?", (term,)):
                if row["path"] not in cached_paths:
                    continue
                document_frequency[term] += 1
                frequencies.setdefault(row["path"], Counter())[term] = row["frequency"]

    candidates: dict[str, SourceText] = {}
    for path in sorted(changed):
        document, skip, problem = read_source(files[path])
        if skip is not None:
            skipped.append(skip)
        if problem is not None:
            problems.append(problem)
        if document is None:
            continue
        count += 1
        total_length += document.token_count
        matched = terms.intersection(document.terms)
        document_frequency.update(matched)
        if matched or _path_match(path, query, target) != "terms":
            candidates[path] = document
            frequencies[path] = Counter({term: document.terms[term] for term in matched})

    candidate_paths = set(candidates) | set(frequencies) | {
        path for path in cached_paths if _path_match(path, query, target) != "terms"
    }

    average_length = total_length / count if count else 1.0
    results: list[dict[str, Any]] = []
    for path in candidate_paths:
        document = candidates.get(path)
        length = cached[path]["token_count"] if document is None else document.token_count
        score = 0.0
        for term, frequency in sorted(frequencies.get(path, {}).items()):
            inverse_frequency = math.log1p((count - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            # Standard BM25 parameters; all file kinds share the same scoring.
            score += inverse_frequency * (frequency * 2.2) / (
                frequency + 1.2 * (0.25 + 0.75 * length / max(average_length, 1.0))
            )
        results.append({
            "path": path, "kind": cached[path]["kind"] if document is None else document.kind,
            "content_sha256": cached[path]["content_sha256"] if document is None else document.content_sha256,
            "match_kind": _path_match(path, query, target),
            "matched_terms": sorted(frequencies.get(path, {})), "score": round(score, 8),
        })
    priorities = {"exact_path": 0, "exact_filename": 1, "terms": 2}
    results.sort(key=lambda result: (priorities[result["match_kind"]], -result["score"], result["path"]))
    returned = results if limit < 0 else results[:limit]
    for result in returned:
        path = result["path"]
        if path in cached_paths:
            text = connection.execute("SELECT body FROM documents WHERE path=?", (path,)).fetchone()["body"]
        else:
            text = candidates[path].text
        line_start, line_end, snippet = _snippet(text, terms, query)
        result.update(line_start=line_start, line_end=line_end, snippet=snippet)
    partial = any(problem.code in {"source_unreadable", "source_changed_during_read", "source_directory_unreadable"} or problem.severity == "error" for problem in problems)
    return {
        "repository": target.to_dict(), "query": query,
        "ranking": {"method": "BM25", "k1": 1.2, "b": 0.75, "path_matches_first": True},
        "results": returned, "match_count": len(results), "returned_count": len(returned),
        "truncated": len(returned) < len(results),
        "coverage": {
            "status": "partial" if partial else "complete",
            "files_considered": len(files), "files_searched": count,
            "skipped_files": sorted(skipped, key=lambda item: (item["path"], item["reason"])),
        },
        "index": {
            "status": "live_scan" if connection is None else "overlay" if replaced else "current",
            "path": source_index_path(root, target).relative_to(root).as_posix(),
            "cached_files": len(cached_paths), "changed_files": len(changed),
            "deleted_files": len(deleted),
        },
    }, problems


def search_sources(
    root: Path, *, target: RepoTarget, query: str, limit: int = 20
) -> tuple[dict[str, Any], list[Problem]]:
    query = query.strip()
    if not query:
        return {}, [Problem("error", "source_query_empty", "source search requires a query")]
    connection, problem = open_source_index(root, target=target)
    problems = [problem] if problem is not None else []
    try:
        try:
            data, search_problems = _search(root, target=target, query=query, limit=limit, connection=connection)
        except sqlite3.Error as exc:
            if connection is None:
                raise
            connection.close()
            connection = None
            problems.append(Problem(
                "warning", "source_index_unavailable",
                f"searching current files because the source index cannot be read: {exc}",
                source_index_path(root, target).relative_to(root).as_posix(),
            ))
            data, search_problems = _search(root, target=target, query=query, limit=limit, connection=None)
        return data, [*problems, *search_problems]
    finally:
        if connection is not None:
            connection.close()


def render_source_search_text(data: dict[str, Any]) -> str:
    lines = [
        f'Source search: {data["query"]}',
        f'{data["returned_count"]} of {data["match_count"]} matches; coverage {data["coverage"]["status"]}; index {data["index"]["status"]}',
    ]
    for result in data["results"]:
        lines.extend((
            "",
            f'{result["path"]}:{result["line_start"]}-{result["line_end"]} ({result["kind"]}, {result["match_kind"]}, BM25 {result["score"]})',
            result["snippet"],
        ))
    return "\n".join(lines)


def render_source_search_markdown(data: dict[str, Any]) -> str:
    lines = [
        f'# Source search: {data["query"]}', "",
        f'{data["returned_count"]} of {data["match_count"]} matches. Coverage: {data["coverage"]["status"]}. Index: {data["index"]["status"]}.',
    ]
    for result in data["results"]:
        fence = "`" * 3
        while fence in result["snippet"]:
            fence += "`"
        lines.extend((
            "", f'## {result["path"]}:{result["line_start"]}-{result["line_end"]}', "",
            f'{result["kind"]}; {result["match_kind"]}; BM25 {result["score"]}.', "",
            fence, result["snippet"], fence,
        ))
    return "\n".join(lines)
