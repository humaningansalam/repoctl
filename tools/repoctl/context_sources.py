from __future__ import annotations

from pathlib import Path
from typing import Any

from .context_chunks import DocumentChunk, chunk_markdown_file, chunk_markdown_text, chunk_text_source
from .context_model import ContextSectionKind, ContextSourceKind
from .git import normalize_repo_path
from .graph_model import GraphSnapshot, digest_data
from .language_profiles import collect_verification_hints, is_context_source_language, language_for_path, product_manifest_patterns
from .inventory import file_inventory
from .repositories import RepoTarget
from .io import Problem
from .task_history import read_task_history


DOCUMENT_PATTERNS = (
    "AGENTS.md",
    "README.md",
    "docs/README.md",
    "docs/BOARD.md",
    "docs/PRD.md",
    "docs/architecture/**/*.md",
    "docs/specs/**/*.md",
    "docs/research/**/*.md",
    "docs/adr/**/*.md",
    "docs/contracts/**/*.md",
    "docs/workflows/**/*.md",
)
PRODUCT_DOCUMENT_PATTERNS = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
    "README.*.md",
    "docs/README.md",
    "docs/PRD.md",
    "docs/architecture/**/*.md",
    "docs/specs/**/*.md",
    "docs/adr/**/*.md",
    "docs/research/**/*.md",
    "docs/contracts/**/*.md",
    "docs/workflows/**/*.md",
)
EXCLUDED_PARTS = {".repoctl-state", "generated", ".next", ".nuxt", ".svelte-kit", ".turbo", ".firebase", ".dart_tool", "Library", "Temp", "Obj", "obj", "Build", "Builds", "Logs", "UserSettings", "node_modules", "dist", "build", "target"}
MAX_CONTEXT_SOURCE_BYTES = 1024 * 1024
CONFIG_SUFFIXES = {
    ".cfg",
    ".conf",
    ".env",
    ".ini",
    ".properties",
}
STRUCTURED_DATA_SUFFIXES = {
    ".json",
    ".jsonc",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}
CONFIG_NAMES = {
    "dockerfile",
    "makefile",
    "procfile",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
}
CONFIG_DOTFILE_EXCLUDES = {".ds_store", ".git", ".gitkeep"}


def context_source_kind(repo_path: str, classification: str) -> ContextSourceKind | None:
    if (
        not repo_path
        or "\\" in repo_path
        or normalize_repo_path(repo_path) != repo_path
        or classification == "excluded"
    ):
        return None
    language = language_for_path(repo_path)
    if is_context_source_language(language):
        return ContextSourceKind.CURRENT_SOURCE
    if language == "markdown":
        return None
    path = Path(repo_path)
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    is_workflow = len(path.parts) >= 3 and tuple(part.casefold() for part in path.parts[:2]) == (".github", "workflows")
    is_dockerfile = name == "dockerfile" or name.startswith("dockerfile.")
    is_config_dotfile = name.startswith(".") and name not in CONFIG_DOTFILE_EXCLUDES
    if name in CONFIG_NAMES or name in CONFIG_SUFFIXES or suffix in CONFIG_SUFFIXES or is_workflow or is_dockerfile or is_config_dotfile:
        return ContextSourceKind.CONFIG
    if suffix in STRUCTURED_DATA_SUFFIXES:
        return ContextSourceKind.STRUCTURED_DATA
    return None


def collect_context_sources(
    root: Path,
    *,
    target: RepoTarget,
    snapshot: GraphSnapshot | None,
    graph_problems: list[Problem],
    graph_meta: dict[str, Any],
    include_history: bool = False,
) -> tuple[list[DocumentChunk], dict[str, str], dict[str, Any], list[Problem]]:
    chunks: list[DocumentChunk] = []
    problems: list[Problem] = []
    document_paths = context_document_paths(root, target=target)
    for path in document_paths:
        try:
            chunks.extend(chunk_markdown_file(root, path))
        except OSError as exc:
            problems.append(Problem("error", "context_source_unreadable", str(exc), path.relative_to(root).as_posix()))
    manifest_paths = context_product_manifest_paths(root, target=target)
    for path in manifest_paths:
        try:
            rel = path.relative_to(root).as_posix()
            chunks.append(
                chunk_text_source(
                    root,
                    rel,
                    path.read_text(encoding="utf-8"),
                    kind="product_manifest",
                    section=path.name,
                    section_kind=ContextSectionKind.CONFIG,
                )
            )
        except UnicodeDecodeError as exc:
            problems.append(Problem("warning", "context_manifest_non_utf8", str(exc), path.relative_to(root).as_posix()))
        except OSError as exc:
            problems.append(Problem("error", "context_manifest_unreadable", str(exc), path.relative_to(root).as_posix()))

    for hint in collect_verification_hints(target.root_path):
        source_path = target.root_path / hint.source_path
        if not source_path.exists():
            continue
        rel = source_path.relative_to(root).as_posix()
        if not hint.command:
            problems.append(Problem("warning", "verification_command_unresolved", hint.reason, rel))
        text = f"Verification command: {hint.command}\nSource: {rel}\nReason: {hint.reason}\nProvider: {hint.provider}"
        chunks.append(
            chunk_text_source(
                root,
                rel,
                text,
                kind="verification_hint",
                section=f"verification: {hint.command}",
                section_kind=ContextSectionKind.VERIFICATION,
            )
        )

    history = []
    history_problems: list[Problem] = []
    if include_history:
        history, history_problems = read_task_history(root, repo_id=target.id)
        for task in history:
            chunks.extend(chunk_markdown_text(root, task.path, task.text, kind="task_artifact"))
    problems.extend(history_problems)
    problems.extend(context_graph_problems(graph_problems, graph_available=snapshot is not None))
    inventory, inventory_problems, _inventory_meta = file_inventory(
        root,
        changed=False,
        target=target,
    )
    current_source_chunks, current_source_problems = _current_source_chunks_from_records(
        root,
        records=[
            (item.workspace_path, item.path, item.classification)
            for item in inventory
        ],
        existing_paths={chunk.source_ref.path for chunk in chunks},
    )
    chunks.extend(current_source_chunks)
    problems.extend(inventory_problems)
    problems.extend(current_source_problems)
    completeness = {
        "documents_checked": len(document_paths),
        "manifests_checked": len(manifest_paths),
        "tasks_checked": len(history),
        "current_sources_checked": len(current_source_chunks),
        "history_loaded": include_history,
        "history_problem_count": len(history_problems),
        "history_problem_paths": [problem.path for problem in history_problems],
        "graph_available": snapshot is not None,
        "graph_meta": graph_meta,
    }
    if snapshot is not None:
        completeness["graph_completeness"] = snapshot.completeness
    source_snapshots = {
        "document_manifest_digest": _manifest_digest([chunk for chunk in chunks if chunk.source_ref.kind in {"document", "product_manifest", "verification_hint"}]),
        "task_manifest_digest": digest_data([task.to_dict() for task in history]),
        "current_source_manifest_digest": _manifest_digest(current_source_chunks),
    }
    if snapshot is not None:
        source_snapshots["graph_digest"] = snapshot.snapshot_digest
    return chunks, source_snapshots, completeness, problems


def _current_source_chunks_from_records(
    root: Path,
    *,
    records: list[tuple[str, str, str]],
    existing_paths: set[str],
) -> tuple[list[DocumentChunk], list[Problem]]:
    chunks: list[DocumentChunk] = []
    problems: list[Problem] = []
    for workspace_path, repo_path, classification in records:
        kind = context_source_kind(repo_path, classification)
        if not kind:
            continue
        if not workspace_path or workspace_path in existing_paths:
            continue
        path = root / workspace_path
        try:
            if not path.is_file():
                continue
            if path.stat().st_size > MAX_CONTEXT_SOURCE_BYTES:
                problems.append(
                    Problem(
                        "warning",
                        "context_source_too_large",
                        f"{kind.value} exceeds {MAX_CONTEXT_SOURCE_BYTES} byte indexing limit",
                        workspace_path,
                    )
                )
                continue
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            problems.append(Problem("warning", "context_source_unreadable", str(exc), workspace_path))
            continue
        if not text.strip():
            continue
        chunks.append(
            chunk_text_source(
                root,
                workspace_path,
                text,
                kind=kind.value,
                section=repo_path or path.name,
                section_kind=(
                    ContextSectionKind.CONFIG
                    if kind == ContextSourceKind.CONFIG
                    else ContextSectionKind.STRUCTURED_DATA
                    if kind == ContextSourceKind.STRUCTURED_DATA
                    else ContextSectionKind.FILE
                ),
            )
        )
    return chunks, problems


def current_source_chunks_for_paths(
    root: Path,
    *,
    target: RepoTarget,
    repo_paths: set[str],
) -> tuple[list[DocumentChunk], list[Problem]]:
    records: list[tuple[str, str, str]] = []
    problems: list[Problem] = []
    for raw_path in sorted(repo_paths):
        repo_path = normalize_repo_path(raw_path)
        if not repo_path or repo_path != raw_path:
            problems.append(
                Problem(
                    "warning",
                    "context_changed_path_invalid",
                    "changed source path is not a normalized repo-relative path",
                    raw_path,
                )
            )
            continue
        if not context_source_kind(repo_path, ""):
            continue
        workspace_path = f"{target.display_path.rstrip('/')}/{repo_path}"
        records.append((workspace_path, repo_path, ""))
    chunks, read_problems = _current_source_chunks_from_records(
        root,
        records=records,
        existing_paths=set(),
    )
    return chunks, [*problems, *read_problems]


def context_overlay_chunks(
    root: Path,
    *,
    target: RepoTarget,
    workspace_paths: set[str],
    include_history: bool,
) -> tuple[list[DocumentChunk], list[Problem]]:
    selected = {str(path) for path in workspace_paths if str(path)}
    chunks: list[DocumentChunk] = []
    problems: list[Problem] = []
    document_paths = {
        path.relative_to(root).as_posix(): path
        for path in context_document_paths(root, target=target)
    }
    manifest_paths = {
        path.relative_to(root).as_posix(): path
        for path in context_product_manifest_paths(root, target=target)
    }
    for rel in sorted(selected & set(document_paths)):
        try:
            chunks.extend(chunk_markdown_file(root, document_paths[rel]))
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem("warning", "context_source_unreadable", str(exc), rel))
    for rel in sorted(selected & set(manifest_paths)):
        try:
            chunks.append(
                chunk_text_source(
                    root,
                    rel,
                    manifest_paths[rel].read_text(encoding="utf-8"),
                    kind="product_manifest",
                    section=manifest_paths[rel].name,
                    section_kind=ContextSectionKind.CONFIG,
                )
            )
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem("warning", "context_manifest_unreadable", str(exc), rel))
    for hint in collect_verification_hints(target.root_path):
        path = target.root_path / hint.source_path
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not hint.command:
            problems.append(Problem("warning", "verification_command_unresolved", hint.reason, rel))
        if rel not in selected:
            continue
        text = f"Verification command: {hint.command}\nSource: {rel}\nReason: {hint.reason}\nProvider: {hint.provider}"
        chunks.append(
            chunk_text_source(
                root,
                rel,
                text,
                kind="verification_hint",
                section=f"verification: {hint.command}",
                section_kind=ContextSectionKind.VERIFICATION,
            )
        )
    if include_history and any(path.startswith(("docs/tasks/", "docs/archive/tasks/")) for path in selected):
        history, history_problems = read_task_history(root, repo_id=target.id)
        problems.extend(history_problems)
        for task in history:
            if task.path in selected:
                chunks.extend(chunk_markdown_text(root, task.path, task.text, kind="task_artifact"))
    return chunks, problems


def context_document_paths(root: Path, *, target: RepoTarget) -> list[Path]:
    paths: set[Path] = set()
    for pattern in DOCUMENT_PATTERNS:
        paths.update(path for path in root.glob(pattern) if path.is_file())
    for pattern in PRODUCT_DOCUMENT_PATTERNS if target.id else []:
        paths.update(path for path in target.root_path.glob(pattern) if path.is_file())
    return sorted(
        path for path in paths
        if not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts)
    )


def context_product_manifest_paths(root: Path, *, target: RepoTarget) -> list[Path]:
    paths: set[Path] = set()
    for pattern in product_manifest_patterns() if target.id else []:
        paths.update(path for path in target.root_path.glob(pattern) if path.is_file())
    return sorted(path for path in paths if not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts))


def context_graph_problems(
    graph_problems: list[Problem],
    *,
    graph_available: bool = False,
) -> list[Problem]:
    mapped: list[Problem] = []
    for problem in graph_problems:
        if graph_available or problem.code != "graph_snapshot_missing":
            mapped.append(problem)
        else:
            mapped.append(
                Problem(
                    "warning",
                    "context_graph_unavailable",
                    f"{problem.message}; source, document, task, and Knowledge evidence remain available",
                    problem.path,
                    problem.code,
                )
            )
    return mapped


def _manifest_digest(chunks: list[DocumentChunk]) -> str:
    return digest_data([chunk.source_ref.to_dict() for chunk in sorted(chunks, key=lambda item: item.source_ref.key())])
