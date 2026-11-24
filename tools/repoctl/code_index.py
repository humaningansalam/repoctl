from __future__ import annotations

import ast
import symtable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .language_profiles import index_dart, language_for_path
from .inventory import InventoryFile, file_inventory
from .repositories import RepoTarget
from .io import Problem


CODE_INDEX_INPUT_VERSION = 12


@dataclass(frozen=True, order=True)
class PythonImportOccurrence:
    raw_import: str
    form: Literal["module", "from"]
    module: str
    imported_name: str = ""
    level: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "raw_import": self.raw_import,
            "form": self.form,
            "module": self.module,
            "imported_name": self.imported_name,
            "level": self.level,
        }


@dataclass(frozen=True)
class CodeIndexEntry:
    path: str
    workspace_path: str
    language: str
    classification: str
    symbols: list[str]
    imports: list[str]
    calls: list[str]
    parse_status: str = "ok"
    parse_error: str = ""
    import_occurrences: tuple[PythonImportOccurrence, ...] = ()
    module_bindings: tuple[str, ...] = ()
    module_certain_bindings: tuple[str, ...] = ()
    module_wildcard_import: bool = False

    def __post_init__(self) -> None:
        for field_name in ("symbols", "imports", "calls"):
            values = getattr(self, field_name)
            object.__setattr__(self, field_name, sorted({value for value in values if value}))
        object.__setattr__(self, "import_occurrences", tuple(sorted(set(self.import_occurrences))))
        object.__setattr__(self, "module_bindings", tuple(sorted({value for value in self.module_bindings if value})))
        object.__setattr__(
            self,
            "module_certain_bindings",
            tuple(sorted({value for value in self.module_certain_bindings if value})),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "workspace_path": self.workspace_path,
            "language": self.language,
            "classification": self.classification,
            "symbols": list(self.symbols),
            "imports": list(self.imports),
            "calls": list(self.calls),
            "parse_status": self.parse_status,
            "import_occurrences": [occurrence.to_dict() for occurrence in self.import_occurrences],
            "module_bindings": list(self.module_bindings),
            "module_certain_bindings": list(self.module_certain_bindings),
            "module_wildcard_import": self.module_wildcard_import,
        }
        if self.parse_error:
            data["parse_error"] = self.parse_error
        return data


def semantic_provider_entries(entries: list[CodeIndexEntry]) -> list[CodeIndexEntry]:
    return [
        entry
        for entry in entries
        if entry.classification != "excluded"
        and (
            entry.parse_status == "ok"
            # These languages are parsed by the structural provider.
            or (entry.parse_status == "skipped" and entry.language in {"javascript", "typescript", "csharp", "rust", "go", "java", "swift"})
        )
    ]


def _dedupe_sorted(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def _python_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _python_call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _python_import_occurrences(tree: ast.Module) -> tuple[PythonImportOccurrence, ...]:
    occurrences: list[PythonImportOccurrence] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            occurrences.extend(
                PythonImportOccurrence(
                    raw_import=alias.name,
                    form="module",
                    module=alias.name,
                )
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            module = node.module or ""
            for alias in node.names:
                raw_import = f"{prefix}{module}.{alias.name}" if module else f"{prefix}{alias.name}"
                occurrences.append(
                    PythonImportOccurrence(
                        raw_import=raw_import,
                        form="from",
                        module=module,
                        imported_name=alias.name,
                        level=node.level,
                    )
                )
    return tuple(sorted(set(occurrences)))


def _module_binding_facts(text: str, tree: ast.Module) -> tuple[tuple[str, ...], tuple[str, ...]]:
    compiler_scope = symtable.symtable(text, "<code-index>", "exec")
    possible = {
        symbol.get_name()
        for symbol in compiler_scope.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace() or symbol.is_declared_global()
    }
    present: set[str] = set()
    uncertain: set[str] = set()

    def target_names(node: ast.AST) -> set[str]:
        if isinstance(node, ast.Name):
            return {node.id}
        if isinstance(node, ast.Starred):
            return target_names(node.value)
        if isinstance(node, ast.Tuple | ast.List):
            return {name for item in node.elts for name in target_names(item)}
        return set()

    def statement_effects(statement: ast.stmt, *, conditional: bool) -> None:
        bound: set[str] = set()
        deleted: set[str] = set()
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(statement.name)
        elif isinstance(statement, ast.Import):
            bound.update(alias.asname or alias.name.split(".", 1)[0] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            bound.update(alias.asname or alias.name for alias in statement.names if alias.name != "*")
        elif isinstance(statement, ast.Assign):
            bound.update(name for target in statement.targets for name in target_names(target))
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            bound.update(target_names(statement.target))
        elif isinstance(statement, ast.AugAssign):
            bound.update(target_names(statement.target))
        elif isinstance(statement, ast.Delete):
            deleted.update(name for target in statement.targets for name in target_names(target))

        if conditional:
            uncertain.update(bound | deleted)
        else:
            present.difference_update(deleted)
            present.update(bound)

        if isinstance(statement, ast.If | ast.For | ast.AsyncFor | ast.While | ast.Try | ast.TryStar | ast.Match | ast.With | ast.AsyncWith):
            def visit_nested(node: ast.AST) -> None:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.stmt):
                        statement_effects(child, conditional=True)
                    elif not isinstance(child, ast.expr):
                        visit_nested(child)

            visit_nested(statement)

    for statement in tree.body:
        statement_effects(statement, conditional=False)
    return tuple(sorted(possible)), tuple(sorted((present - uncertain) & possible))


def _index_python(
    text: str,
) -> tuple[
    list[str],
    list[str],
    list[str],
    str,
    str,
    tuple[PythonImportOccurrence, ...],
    tuple[str, ...],
    tuple[str, ...],
    bool,
]:
    try:
        tree = ast.parse(text)
        module_bindings, module_certain_bindings = _module_binding_facts(text, tree)
    except SyntaxError as exc:
        return [], [], [], "parse_error", exc.msg, (), (), (), False

    symbols: list[str] = []
    imports: list[str] = []
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            symbols.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            module = node.module or ""
            base = f"{prefix}{module}"
            imports.extend(f"{base}.{alias.name}" if module else f"{prefix}{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            calls.append(_python_call_name(node.func))
    return (
        _dedupe_sorted(symbols),
        _dedupe_sorted(imports),
        _dedupe_sorted(calls),
        "ok",
        "",
        _python_import_occurrences(tree),
        module_bindings,
        module_certain_bindings,
        any(
            isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
            for node in ast.walk(tree)
        ),
    )


def _index_file(repo: Path, file: InventoryFile) -> CodeIndexEntry:
    language = language_for_path(file.path)
    if file.classification == "excluded":
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], "skipped", "excluded from source indexing")
    if language in {"javascript", "typescript"}:
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], "skipped", "TypeScript provider required")
    if language == "csharp":
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], "skipped", "C# provider required")
    if language not in {"python", "dart"}:
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], "skipped", "unsupported language")

    path = repo / file.path
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], "skipped", "non-utf8 file")
    except OSError as exc:
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], "parse_error", str(exc))

    if language == "python":
        (
            symbols,
            imports,
            calls,
            status,
            error,
            import_occurrences,
            module_bindings,
            module_certain_bindings,
            module_wildcard_import,
        ) = _index_python(text)
    else:
        symbols, imports, calls, status, error = index_dart(text)
        import_occurrences = ()
        module_bindings = ()
        module_certain_bindings = ()
        module_wildcard_import = False

    return CodeIndexEntry(
        file.path,
        file.workspace_path,
        language,
        file.classification,
        symbols,
        imports,
        calls,
        status,
        error,
        import_occurrences,
        module_bindings,
        module_certain_bindings,
        module_wildcard_import,
    )


def _reused_index_entry(file: InventoryFile, previous: CodeIndexEntry) -> CodeIndexEntry:
    return CodeIndexEntry(
        path=file.path,
        workspace_path=file.workspace_path,
        language=language_for_path(file.path),
        classification=file.classification,
        symbols=list(previous.symbols),
        imports=list(previous.imports),
        calls=list(previous.calls),
        parse_status=previous.parse_status,
        parse_error=previous.parse_error,
        import_occurrences=previous.import_occurrences,
        module_bindings=previous.module_bindings,
        module_certain_bindings=previous.module_certain_bindings,
        module_wildcard_import=previous.module_wildcard_import,
    )


def build_code_index_from_inventory(
    *,
    files: list[InventoryFile],
    inventory_problems: list[Problem],
    inventory_meta: dict[str, Any],
    target: RepoTarget,
    previous_entries: list[CodeIndexEntry] | None = None,
    reindex_paths: set[str] | None = None,
    limit: int = -1,
) -> tuple[list[CodeIndexEntry], list[Problem], dict[str, Any]]:
    if inventory_problems:
        return [], inventory_problems, inventory_meta
    previous_by_path = {entry.path: entry for entry in previous_entries or []}
    reindex_paths = reindex_paths or set()
    entries: list[CodeIndexEntry] = []
    reindexed = 0
    reused = 0
    ordered_files = sorted(files, key=lambda file: file.path)
    total_before_limit = len(ordered_files)
    selected_files = ordered_files if limit < 0 else ordered_files[:limit]
    for file in selected_files:
        previous = previous_by_path.get(file.path)
        excluded_boundary_changed = previous is not None and ((previous.classification == "excluded") != (file.classification == "excluded"))
        can_reuse = (
            previous is not None
            and file.path not in reindex_paths
            and previous.language == language_for_path(file.path)
            and not excluded_boundary_changed
        )
        if can_reuse:
            entries.append(_reused_index_entry(file, previous))
            reused += 1
        else:
            entries.append(_index_file(target.root_path, file))
            reindexed += 1
    returned = len(entries)
    summary = {
        "total": total_before_limit,
        "returned": returned,
        "truncated": returned < total_before_limit,
        "dropped_count": max(0, total_before_limit - returned),
        "ok": sum(1 for entry in entries if entry.parse_status == "ok"),
        "skipped": sum(1 for entry in entries if entry.parse_status == "skipped"),
        "parse_error": sum(1 for entry in entries if entry.parse_status == "parse_error"),
        "reindexed": reindexed,
        "reused": reused,
        "languages": {
            language: sum(1 for entry in entries if entry.language == language)
            for language in sorted({entry.language for entry in entries})
        },
    }
    return entries, inventory_problems, {**inventory_meta, "summary": summary}


def build_code_index(root: Path, *, changed: bool = False, limit: int = 200, target: RepoTarget | None = None) -> tuple[list[CodeIndexEntry], list[Problem], dict[str, Any]]:
    from .repositories import require_repo_target

    target = target or require_repo_target(root)
    files, problems, meta = file_inventory(root, changed=changed, target=target)
    return build_code_index_from_inventory(files=files, inventory_problems=problems,
                                           inventory_meta=meta, target=target, limit=limit)
