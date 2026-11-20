from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .inventory import file_inventory
from .io import Problem
from .language_profiles import is_semantic_source_language, language_for_path
from .repositories import RepoTarget


INDEX_SCHEMA = "repoctl.source-index"
INDEX_VERSION = 1


@dataclass(frozen=True)
class SourceFile:
    path: str
    absolute_path: Path
    kind: str


@dataclass(frozen=True)
class SourceText:
    path: str
    kind: str
    text: str
    content_sha256: str
    signature: str
    terms: Counter[str]

    @property
    def token_count(self) -> int:
        return self.terms.total()


def tokenize(text: str) -> list[str]:
    """Unicode words, with snake_case and CamelCase identifier boundaries."""
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return re.findall(r"[^\W_]+", text.casefold())


def _is_record_path(path: str) -> bool:
    parts = Path(path).parts
    return (
        ".repoctl-state" in parts
        or path == "docs/BOARD.md"
        or path.startswith(("docs/tasks/", "docs/archive/tasks/", "docs/knowledge/"))
    )


def _source_kind(path: str) -> str:
    language = language_for_path(path)
    if is_semantic_source_language(language):
        return "source"
    suffix = Path(path).suffix.casefold()
    if suffix in {".md", ".rst", ".adoc", ".txt", ".html"}:
        return "document"
    if suffix in {".json", ".jsonl", ".csv", ".tsv", ".xml"}:
        return "data"
    if suffix in {".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".properties"}:
        return "config"
    return "text"


def collect_source_files(
    root: Path, *, target: RepoTarget
) -> tuple[dict[str, SourceFile], list[dict[str, str]], list[Problem]]:
    files: dict[str, SourceFile] = {}
    skipped: list[dict[str, str]] = []
    problems: list[Problem] = []
    if target.id:
        inventory, inventory_problems, _meta = file_inventory(root, target=target)
        problems.extend(inventory_problems)
        for item in inventory:
            if item.classification == "excluded" or _is_record_path(item.workspace_path):
                skipped.append({"path": item.workspace_path, "reason": "excluded"})
                continue
            files[item.workspace_path] = SourceFile(
                item.workspace_path, target.root_path / item.path, _source_kind(item.path)
            )

    if target.id and target.root_path.resolve() == root.resolve():
        return files, skipped, problems
    documents = [root / name for name in ("AGENTS.md", "README.md", "CONTRIBUTING.md")]
    def directory_error(error: OSError) -> None:
        if isinstance(error, FileNotFoundError) and Path(error.filename or "") == root / "docs":
            return
        problems.append(Problem(
            "warning", "source_directory_unreadable", str(error), str(error.filename or root / "docs")
        ))
    for directory, subdirectories, filenames in os.walk(root / "docs", onerror=directory_error):
        base = Path(directory)
        for name in list(subdirectories):
            relative = (base / name).relative_to(root).as_posix() + "/"
            if _is_record_path(relative):
                subdirectories.remove(name)
                skipped.append({"path": relative, "reason": "record"})
        documents.extend(base / name for name in filenames)
    for path in sorted(documents):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in files:
            continue
        if _is_record_path(relative):
            skipped.append({"path": relative, "reason": "record"})
            continue
        if not path.resolve().is_relative_to(root.resolve()):
            skipped.append({"path": relative, "reason": "outside_workspace"})
            continue
        files[relative] = SourceFile(relative, path, _source_kind(relative))
    return files, skipped, problems


def file_signature(path: Path) -> str:
    value = path.stat()
    return ":".join(str(part) for part in (
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
    ))


def read_source(source: SourceFile) -> tuple[SourceText | None, dict[str, str] | None, Problem | None]:
    try:
        before = file_signature(source.absolute_path)
        raw = source.absolute_path.read_bytes()
        after = file_signature(source.absolute_path)
    except OSError as exc:
        return None, {"path": source.path, "reason": "unreadable"}, Problem(
            "warning", "source_unreadable", str(exc), source.path
        )
    if before != after:
        return None, {"path": source.path, "reason": "changed_during_read"}, Problem(
            "warning", "source_changed_during_read",
            "source changed while it was being read", source.path
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, {"path": source.path, "reason": "non_utf8"}, None
    if "\x00" in text:
        return None, {"path": source.path, "reason": "binary"}, None
    return SourceText(
        source.path, source.kind, text,
        "sha256:" + hashlib.sha256(raw).hexdigest(), after,
        Counter(tokenize(source.path + "\n" + text)),
    ), None, None


def source_index_path(root: Path, target: RepoTarget) -> Path:
    return root / ".repoctl-state/graph" / (target.id or "_workspace") / "source-index.sqlite3"


def _metadata(target: RepoTarget) -> dict[str, Any]:
    return {"schema": INDEX_SCHEMA, "version": INDEX_VERSION, "repository": target.to_dict()}


def _path_label(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def open_source_index(
    root: Path, *, target: RepoTarget, database_path: Path | None = None
) -> tuple[sqlite3.Connection | None, Problem | None]:
    path = database_path or source_index_path(root, target)
    if not path.is_file():
        return None, None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        row = connection.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
        if row is None or json.loads(row["value"]) != _metadata(target):
            raise ValueError("source index format or repository identity does not match")
        connection.execute("SELECT path, kind, signature, content_sha256, body, token_count FROM documents LIMIT 0")
        connection.execute("SELECT term, path, frequency FROM terms LIMIT 0")
        return connection, None
    except (OSError, sqlite3.Error, ValueError) as exc:
        if connection is not None:
            connection.close()
        return None, Problem(
            "warning", "source_index_unavailable",
            f"searching current files because the source index cannot be read: {exc}",
            _path_label(root, path),
        )


def _initialize(connection: sqlite3.Connection, target: RepoTarget) -> None:
    connection.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE documents (
            path TEXT PRIMARY KEY, kind TEXT NOT NULL, signature TEXT NOT NULL,
            content_sha256 TEXT NOT NULL, body TEXT NOT NULL, token_count INTEGER NOT NULL
        );
        CREATE TABLE terms (
            term TEXT NOT NULL, path TEXT NOT NULL, frequency INTEGER NOT NULL,
            PRIMARY KEY (term, path), FOREIGN KEY (path) REFERENCES documents(path) ON DELETE CASCADE
        );
        CREATE INDEX terms_path ON terms(path);
    """)
    connection.execute(
        "INSERT INTO metadata VALUES ('identity', ?)",
        (json.dumps(_metadata(target), ensure_ascii=False, sort_keys=True),),
    )
    connection.commit()


def materialize_source_index(
    root: Path, *, target: RepoTarget, database_path: Path | None = None, rebuild: bool = False
) -> tuple[dict[str, Any], list[Problem]]:
    path = database_path or source_index_path(root, target)
    files, skipped, problems = collect_source_files(root, target=target)
    result: dict[str, Any] = {"path": _path_label(root, path), "status": "failed"}
    if any(problem.severity == "error" for problem in problems):
        return result, problems
    existing, index_problem = open_source_index(root, target=target, database_path=path)
    reset = rebuild or existing is None
    if existing is not None:
        existing.close()
    if index_problem is not None:
        problems.append(index_problem)
    connection: sqlite3.Connection | None = None
    updated: list[str] = []
    removed: list[str] = []
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if reset:
            path.unlink(missing_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if reset:
            _initialize(connection, target)
        cached = {
            row["path"]: (row["signature"], row["kind"])
            for row in connection.execute("SELECT path, signature, kind FROM documents")
        }
        with connection:
            for cached_path in sorted(set(cached) - set(files)):
                connection.execute("DELETE FROM documents WHERE path=?", (cached_path,))
                removed.append(cached_path)
            for source in files.values():
                try:
                    unchanged = cached.get(source.path) == (file_signature(source.absolute_path), source.kind)
                except OSError:
                    unchanged = False
                if unchanged:
                    continue
                document, skip, problem = read_source(source)
                connection.execute("DELETE FROM documents WHERE path=?", (source.path,))
                if skip is not None:
                    skipped.append(skip)
                if problem is not None:
                    problems.append(problem)
                if document is None:
                    continue
                connection.execute(
                    "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                    (document.path, document.kind, document.signature,
                     document.content_sha256, document.text, document.token_count),
                )
                connection.executemany(
                    "INSERT INTO terms VALUES (?, ?, ?)",
                    ((term, document.path, count) for term, count in document.terms.items()),
                )
                updated.append(document.path)
        count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        result.update(
            status="rebuilt" if reset else "updated",
            files_indexed=count, updated_paths=sorted(updated),
            deleted_paths=removed, skipped_files=skipped,
        )
    except (OSError, sqlite3.Error) as exc:
        problems.append(Problem("warning", "source_index_write_failed", str(exc), _path_label(root, path)))
    finally:
        if connection is not None:
            connection.close()
    return result, problems
