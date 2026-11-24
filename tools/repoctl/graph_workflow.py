"""Locate route and symbol implementations through Graph edges and source references."""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from .graph_model import GraphNode, GraphSnapshot
from .git import normalize_repo_path
from .path_roles import is_test_path
from .repositories import RepoTarget

_ROUTE_METHODS = frozenset({"route", "api_route", "get", "post", "put", "patch", "delete"})
_FALLBACK_SUFFIXES = frozenset({".rs", ".go", ".java", ".kt", ".kts", ".swift"})


def _source(target: RepoTarget, path: str) -> tuple[str | None, int, str]:
    """Return text, bytes read, and a reason when source cannot be inspected."""
    if not path or normalize_repo_path(path) != path:
        return None, 0, "invalid_path"
    file = target.root_path / path
    raw = b""
    try:
        base = target.root_path.resolve()
        resolved = file.resolve()
        if base not in (resolved, *resolved.parents):
            return None, 0, "outside_repository"
        if file.is_symlink():
            return None, 0, "symlink"
        if not file.is_file():
            return None, 0, "not_regular_file"
        before = file.stat()
        raw = file.read_bytes()
        after = file.stat()
        if (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns
        ) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ) or len(raw) != before.st_size:
            return None, len(raw), "file_changed_during_read"
    except (OSError, ValueError, RuntimeError):
        return None, len(raw), "read_error"
    try:
        return raw.decode("utf-8"), len(raw), ""
    except UnicodeError:
        return None, len(raw), "decode_error"


def _node_path_index(snapshot: GraphSnapshot) -> tuple[dict[str, dict[str, str]], dict[str, GraphNode]]:
    nodes = {node.id: node for node in snapshot.nodes}
    owner = {
        edge.to_id: str(nodes[edge.from_id].identity.get("path") or "")
        for edge in snapshot.edges
        if edge.kind == "DEFINES" and edge.from_id in nodes and edge.to_id in nodes
    }
    symbols = {
        node.id: {
            "name": str((node.facts.get("provider") or {}).get("qualified_name") or ""),
            "path": owner.get(node.id, ""),
            "kind": str((node.facts.get("provider") or {}).get("kind") or ""),
        }
        for node in snapshot.nodes
        if node.kind == "symbol" and isinstance(node.facts.get("provider"), dict)
    }
    return symbols, nodes


def _route_functions(tree: ast.AST | None, route: str) -> list[tuple[str, int, ast.AST]]:
    if tree is None:
        return []
    matches: list[tuple[str, int, ast.AST]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr not in _ROUTE_METHODS or not decorator.args:
                continue
            first = decorator.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value == route:
                matches.append((node.name, node.lineno, node))
    return matches


def discover_route_entrypoints(
    seed_files: list[str], python_tree: Any, route: str,
) -> tuple[list[dict[str, Any]], list[tuple[str, str, ast.AST]]]:
    """Discover exact route decorators without deriving meaning from filenames."""
    entries: list[dict[str, Any]] = []
    functions: list[tuple[str, str, ast.AST]] = []
    for path in seed_files:
        if not path.endswith(".py") or is_test_path(path):
            continue
        for name, line, function in _route_functions(python_tree(path), route):
            entries.append(_entry(path, relation="source", evidence="HTTP decorator literal", symbol=name, line=line))
            functions.append((path, name, function))
    return entries, functions


def collect_route_value_flow(function: ast.AST) -> set[tuple[str, str]]:
    """Find calls contributing values to a handler's returned response.

    Follow local assignments through response wrappers. These source
    dependencies identify candidates; they do not resolve dynamic dispatch.
    """
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()

    def trace(expr: ast.AST, bindings: dict[str, ast.AST]) -> set[tuple[str, str]]:
        start = (expr, frozenset())
        values: dict[tuple[ast.AST, frozenset[str]], set[tuple[str, str]]] = {}
        pending = [(*start, False)]
        while pending:
            node, seen, expanded = pending.pop()
            key = (node, seen)
            if key in values:
                continue
            children: list[tuple[ast.AST, frozenset[str]]] = []
            fallback: set[tuple[str, str]] = set()
            if isinstance(node, ast.Name):
                if node.id not in seen and node.id in bindings:
                    children = [(bindings[node.id], seen | {node.id})]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                receiver = node.func.value
                if isinstance(receiver, ast.Name) and receiver.id in bindings:
                    bound = bindings[receiver.id]
                    if not (isinstance(bound, ast.Call) and isinstance(bound.func, ast.Name)):
                        children = [(receiver, seen)]
                elif isinstance(receiver, ast.Call):
                    children = [(receiver, seen)]
                fallback = {(node.func.attr, ast.unparse(receiver))}
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                children = [(arg, seen) for arg in [
                    *node.args, *(keyword.value for keyword in node.keywords),
                ]]
                fallback = {(node.func.id, "")}
            elif not isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
                children = [(child, seen) for child in ast.iter_child_nodes(node)]
            if children and not expanded:
                pending.append((node, seen, True))
                pending.extend((child, child_seen, False) for child, child_seen in children)
                continue
            upstream: set[tuple[str, str]] = set()
            for child in children:
                upstream.update(values[child])
            values[key] = upstream or fallback
        return values[start]

    result: set[tuple[str, str]] = set()
    blocks: list[tuple[list[ast.stmt], dict[str, ast.AST]]] = [(function.body, {})]
    while blocks:
        statements, bindings = blocks.pop()
        for statement in statements:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = statement.value
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                if statement.value is not None:
                    bindings[statement.target.id] = statement.value
            elif isinstance(statement, ast.Return) and statement.value is not None:
                result.update(trace(statement.value, bindings))
            elif isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While)):
                blocks.extend((body, bindings.copy()) for body in [statement.body, statement.orelse])
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                blocks.append((statement.body, bindings.copy()))
            elif isinstance(statement, ast.Try):
                blocks.extend((body, bindings.copy()) for body in [
                    statement.body, *(handler.body for handler in statement.handlers),
                    statement.orelse, statement.finalbody,
                ])
    return result


def _python_test_operation_evidence(
    tree: ast.AST | None, *, method: str, route: str,
    implementation_path: str = "", implementation_class: str = "",
) -> tuple[str, int] | None:
    """Verify a test's literal HTTP request or imported concrete manager call.

    Similar method names and a module import alone are never evidence that a
    test exercises this route or class. This is AST source evidence, not a
    claim that the test executed successfully.
    """
    if tree is None:
        return None
    implementation_module = implementation_path.removesuffix(".py").replace("/", ".")
    imported_names = {
        alias.asname or alias.name
        for node in tree.body if isinstance(node, ast.ImportFrom)
        and node.module == implementation_module
        for alias in node.names if alias.name == implementation_class
    } if implementation_module and implementation_class else set()
    for test in tree.body:
        if not isinstance(test, (ast.FunctionDef, ast.AsyncFunctionDef)) or not test.name.startswith("test_"):
            continue
        parameters = {arg.arg for arg in test.args.args}
        nodes = list(ast.walk(test))
        for node in nodes:
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if (node.func.attr in {"get", "post", "put", "patch", "delete"}
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in parameters
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value.split("?", 1)[0].split("#", 1)[0] == route):
                return "python_literal_http_test", node.lineno
        if not imported_names:
            continue
        constructed = set()
        for node in nodes:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
                continue
            if value.func.id not in imported_names:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            constructed.update(target.id for target in targets if isinstance(target, ast.Name))
        for node in nodes:
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != method:
                continue
            receiver = node.func.value
            if (isinstance(receiver, ast.Name) and receiver.id in constructed
                    or isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Name)
                    and receiver.func.id in imported_names):
                return "python_imported_class_method_test", node.lineno
    return None


def _rust_embedded_test_evidence(source: str) -> tuple[str, int] | None:
    """Find an AST-validated #[test] referring to a declaration in this module.

    Identical words in other crates, comments and string literals do not
    establish that a test exercises this Rust implementation.
    """
    try:
        from tree_sitter_language_pack import get_parser
        text = source.encode("utf-8")
        root = get_parser("rust").parse(text).root_node
    except (ImportError, OSError, RuntimeError, ValueError):
        return None
    if root.has_error:
        return None
    definitions: set[str] = set()
    for child in root.named_children:
        if child.type not in {"struct_item", "enum_item", "trait_item", "function_item", "type_item"}:
            continue
        name = child.child_by_field_name("name")
        if name is not None:
            definitions.add(text[name.start_byte:name.end_byte].decode("utf-8"))
    if not definitions:
        return None
    scopes = [root]
    while scopes:
        scope = scopes.pop()
        attributes: list[str] = []
        for node in scope.named_children:
            if node.type == "attribute_item":
                attributes.append(text[node.start_byte:node.end_byte].decode("utf-8"))
                continue
            marked_test = any(re.fullmatch(r"#\[test\]", attr.strip()) for attr in attributes)
            attributes.clear()
            if node.type == "mod_item":
                body = node.child_by_field_name("body")
                if body is not None:
                    scopes.append(body)
            if node.type != "function_item" or not marked_test:
                continue
            name = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name is None or body is None:
                continue
            pending = [body]
            while pending:
                item = pending.pop()
                if item.type in {"identifier", "type_identifier"}:
                    identifier = text[item.start_byte:item.end_byte].decode("utf-8")
                    if identifier in definitions:
                        return (text[name.start_byte:name.end_byte].decode("utf-8"),
                                text.count(b"\n", 0, node.start_byte) + 1)
                pending.extend(item.named_children)
    return None


def _current_class_method(tree: ast.AST | None, qualified: str, method: str) -> bool:
    """Check a declaration before offering it as an unresolved call candidate."""
    if tree is None or "." not in qualified:
        return False
    class_name = qualified.split(".", 1)[0]
    return any(
        isinstance(cls, ast.ClassDef) and cls.name == class_name
        and any(isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name == method
                for member in cls.body)
        for cls in ast.walk(tree)
    )


def collect_test_references(
    paths: list[str],
    *,
    test_reference: Any,
) -> list[dict[str, Any]]:
    """Find source references to the selected operation in test files."""
    python_test_paths = sorted(path for path in paths if is_test_path(path) and path.endswith(".py"))
    referenced: list[dict[str, Any]] = []
    for path in python_test_paths:
        reference = test_reference(path)
        if not reference:
            continue
        entry = _entry(
            path, relation="source",
            evidence="AST request or imported implementation call",
            line=reference[1],
        )
        entry["reference_kind"] = reference[0]
        referenced.append(entry)
    return referenced


def _entry(path: str, *, relation: str, evidence: str, symbol: str = "", line: int = 0) -> dict[str, Any]:
    item: dict[str, Any] = {"path": path, "relation": relation, "evidence": evidence}
    if symbol:
        item["symbol"] = symbol
    if line:
        item["line"] = line
    return item


def _dedupe_entries(entries: list[dict[str, Any]], *, preferred_symbol: str = "") -> tuple[list[dict[str, Any]], int]:
    # Multiple relevant tests in one file should not create one item per test.
    merged: dict[str, dict[str, Any]] = {}
    for item in entries:
        key = str(item.get("path") or "")
        previous = merged.get(key)
        if previous is None or (previous["relation"] == "candidate" and item["relation"] != "candidate"):
            merged[key] = item
    # Keep the queried symbol ahead of tangential matches. A route's primary
    # implementation is the first navigation action, not alphabetic filename.
    result = list(merged.values())
    result.sort(key=lambda item: (
        0 if preferred_symbol and item.get("symbol", "").rsplit(".", 1)[-1] == preferred_symbol else 1,
        {"resolved": 0, "source": 1, "candidate": 2}.get(item["relation"], 3),
        item["path"],
    ))
    return result, len(result)


def project_workflow_completeness(
    *,
    route: str,
    symbol: str,
    all_files: list[str],
    entrypoints: list[dict[str, Any]],
    implementations: list[dict[str, Any]],
    dependents: list[dict[str, Any]],
    tests: list[dict[str, Any]],
    freshness: dict[str, Any],
    skipped_sources: dict[str, str],
) -> tuple[str, list[str]]:
    """Report incomplete input coverage and unresolved references."""
    candidate_present = any(
        entry["relation"] == "candidate"
        for entry in [*implementations, *dependents, *tests]
    )
    freshness_status = str(freshness.get("status") or "current")
    partial = (
        candidate_present or skipped_sources
        or freshness_status != "current"
    )
    if entrypoints or implementations:
        status = "partial" if partial else "found"
    elif freshness_status != "current" or skipped_sources:
        status = "partial"
    elif symbol and any(Path(path).suffix.casefold() in _FALLBACK_SUFFIXES for path in all_files):
        status = "unsupported"
    else:
        status = "not_found"
    warnings: list[str] = []
    if skipped_sources:
        warnings.append("Some source paths could not be inspected; see completeness.skipped_sources")
    if freshness_status != "current":
        warnings.append("Graph inputs have changed; stale relationships are excluded")
    if route and len({entry["path"] for entry in implementations}) > 1:
        warnings.append("Multiple implementation paths are shown; unresolved calls remain candidates")
    return status, warnings


def assemble_workflow_result(
    *,
    route: str,
    symbol: str,
    in_file: str,
    stages: dict[str, list[dict[str, Any]]],
    counts: dict[str, int],
    links: list[dict[str, Any]],
    all_files: list[str],
    files_scanned: int,
    read_bytes: int,
    freshness: dict[str, Any],
    skipped_sources: dict[str, str],
) -> dict[str, Any]:
    """Assemble navigation results with observed source coverage."""
    status, warnings = project_workflow_completeness(
        route=route, symbol=symbol, all_files=all_files,
        entrypoints=stages["entrypoints"],
        implementations=stages["implementation"],
        dependents=stages["callers_or_dependents"],
        tests=stages["tests"],
        freshness=freshness, skipped_sources=skipped_sources,
    )
    return {
        "query": {"route": route, "symbol": symbol, "in_file": in_file},
        "status": status,
        "stages": stages,
        "counts": counts,
        "links": links,
        "completeness": {
            "source_scan_complete": not skipped_sources,
            "skipped_sources": [{"path": path, "reason": reason} for path, reason in sorted(skipped_sources.items())],
            "source_file_count": len(all_files),
            "files_scanned": files_scanned,
            "source_bytes_scanned": read_bytes,
            "graph_freshness": str(freshness.get("status") or "unknown"),
        },
        "warnings": warnings,
    }


def query_graph_workflow(
    snapshot: GraphSnapshot,
    *,
    target: RepoTarget,
    route: str = "",
    symbol: str = "",
    in_file: str = "",
    freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return source-anchored navigation without treating candidates as precise edges."""
    if bool(route) == bool(symbol):
        return {"status": "invalid", "problem": "choose exactly one --route or --symbol"}
    if in_file and (normalize_repo_path(in_file) != in_file or in_file.startswith("/")):
        return {"status": "invalid", "problem": "--in-file must be a normalized repository-relative file"}
    freshness = freshness or {}
    stale = set(freshness.get("stale_paths") or [])
    symbols, nodes = _node_path_index(snapshot)
    graph_files = {
        str(node.identity.get("path") or "")
        for node in snapshot.nodes
        if node.kind == "file" and str(node.identity.get("path") or "")
    }
    changed = {str(path) for path in freshness.get("changed_paths") or [] if isinstance(path, str)}
    # A stale, nearly empty Graph can still point at changed current files.
    all_files = sorted(graph_files | changed)
    # A current file missing from a stale Graph is still a valid exact seed.
    if in_file:
        files = [in_file, *(path for path in all_files if path != in_file)]
    else:
        files = all_files
    seed_files = [in_file] if in_file else files
    source_cache: dict[str, str] = {}
    tree_cache: dict[str, ast.AST | None] = {}
    skipped_sources: dict[str, str] = {}
    read_bytes = 0
    files_read = 0

    def source_read(path: str) -> str:
        nonlocal read_bytes, files_read
        if path not in source_cache:
            value, size, reason = _source(target, path)
            read_bytes += size
            if value is not None or size:
                files_read += 1
            if reason:
                skipped_sources[path] = reason
            source_cache[path] = value or ""
        return source_cache[path]

    def python_tree(path: str) -> ast.AST | None:
        if path not in tree_cache:
            value = source_read(path)
            tree_cache[path] = None
            if path not in skipped_sources:
                try:
                    tree_cache[path] = ast.parse(value)
                except (SyntaxError, ValueError, RecursionError):
                    skipped_sources[path] = "parse_error"
        return tree_cache[path]

    entrypoints: list[dict[str, Any]] = []
    implementations: list[dict[str, Any]] = []
    dependents: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    chosen_symbol_ids: set[str] = set()
    entrypoint_source: list[tuple[str, str, ast.AST]] = []
    if route:
        entrypoints, entrypoint_source = discover_route_entrypoints(seed_files, python_tree, route)
        if len(entrypoints) > 1:
            return {
                "query": {"route": route, "in_file": in_file},
                "status": "ambiguous", "entrypoints": entrypoints,
                "warnings": ["Multiple entrypoints match; pass --in-file to disambiguate"],
                "completeness": {
                    "source_scan_complete": not skipped_sources,
                    "source_file_count": len(files), "files_scanned": files_read,
                    "source_bytes_scanned": read_bytes,
                    "skipped_sources": [{"path": path, "reason": reason} for path, reason in sorted(skipped_sources.items())],
                },
            }
        for path, name, _function in entrypoint_source:
            chosen_symbol_ids.update(
                symbol_id for symbol_id, info in symbols.items()
                if info["path"] == path and info["name"].rsplit(".", 1)[-1] == name and path not in stale
            )
        # Use provider CALLS edges for resolved targets. Attribute names and
        # application-factory assignments do not establish dynamic dispatch.
        route_symbol_ids = chosen_symbol_ids.copy()
        for edge in snapshot.edges:
            if edge.kind != "CALLS" or edge.from_id not in route_symbol_ids:
                continue
            info = symbols.get(edge.to_id, {})
            path = info.get("path", "")
            if not path or path in stale or is_test_path(path):
                continue
            chosen_symbol_ids.add(edge.to_id)
            implementations.append(_entry(path, relation="resolved", evidence="CALLS", symbol=info["name"]))
            links.append({"from": symbols[edge.from_id]["path"], "to": path,
                          "relation": "resolved", "evidence": "CALLS"})
        if entrypoint_source:
            route_path, _name, function = entrypoint_source[0]
            methods = {method for method, receiver in collect_route_value_flow(function) if receiver}
            for symbol_id, info in symbols.items():
                if symbol_id in chosen_symbol_ids or not info["path"].endswith(".py") or is_test_path(info["path"]):
                    continue
                method = info["name"].rsplit(".", 1)[-1]
                if method not in methods or not _current_class_method(python_tree(info["path"]), info["name"], method):
                    continue
                chosen_symbol_ids.add(symbol_id)
                implementations.append(_entry(info["path"], relation="candidate",
                                               evidence="matching declaration for an unresolved attribute call",
                                               symbol=info["name"]))
                links.append({"from": route_path, "to": info["path"], "relation": "candidate",
                              "evidence": "unresolved attribute call"})
    else:
        matches = [
            (symbol_id, info) for symbol_id, info in symbols.items()
            if info["path"] and (info["name"] == symbol or info["name"].rsplit(".", 1)[-1] == symbol)
            and (not in_file or info["path"] == in_file)
            and info["path"] not in stale
        ]
        if len(matches) > 1:
            return {
                "query": {"symbol": symbol, "in_file": in_file},
                "status": "ambiguous", "implementations": [
                    _entry(info["path"], relation="resolved", evidence="provider symbol", symbol=info["name"])
                    for _symbol_id, info in matches
                ], "warnings": ["Multiple precise symbols match; pass a qualified name and --in-file"],
            }
        for symbol_id, info in matches:
            # Rust `mod audio_clock;` declares the module file, rather than
            # implementing the code inside the declaring main.rs.
            if info.get("kind") == "module":
                linked_paths = {
                    str(nodes[edge.to_id].identity.get("path") or "")
                    for edge in snapshot.edges
                    if edge.kind == "IMPORTS_FILE" and edge.source == "tree_sitter_structural"
                    and edge.from_id in nodes and edge.to_id in nodes
                    and str(nodes[edge.from_id].identity.get("path") or "") == info["path"]
                    and (
                        Path(str(nodes[edge.to_id].identity.get("path") or "")).stem == symbol
                        or str(nodes[edge.to_id].identity.get("path") or "").endswith("/"+symbol+"/mod.rs")
                    )
                }
                if len(linked_paths) == 1:
                    implementations.append(_entry(next(iter(linked_paths)), relation="resolved",
                                                  evidence="resolved Rust mod file", symbol=info["name"]))
                    continue
            chosen_symbol_ids.add(symbol_id)
            implementations.append(_entry(info["path"], relation="resolved", evidence="provider symbol", symbol=info["name"]))
        if not matches and symbol:
            # Unsupported languages still get source locations, never a
            # fabricated precise symbol, caller, or zero-impact assertion.
            # Accept snake_case test names such as host_audio_clock_default,
            # but not an unrelated longer alphanumeric word such as mainline.
            needle = re.compile(r"(?<![A-Za-z0-9])" + re.escape(symbol.rsplit(".", 1)[-1]) + r"(?![A-Za-z0-9])")
            for path in seed_files:
                suffix = Path(path).suffix.casefold()
                # Dart can remain queryable as source evidence even on a
                # machine without a Dart SDK/analyzer. Only the compiler
                # provider may elevate a Dart symbol to `resolved`.
                if suffix not in _FALLBACK_SUFFIXES and suffix != ".dart" and not (
                    str(freshness.get("status") or "current") != "current" and suffix == ".py"
                ):
                    continue
                source = source_read(path)
                matched_line = next((i for i, line in enumerate(source.splitlines(), 1) if needle.search(line)), 0)
                if matched_line or symbol.casefold() in Path(path).stem.casefold():
                    if suffix == ".rs" and re.search(
                        r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:mod\s+" + re.escape(symbol.rsplit(".", 1)[-1])
                        + r"\s*;|use\s+[^;]*\b" + re.escape(symbol.rsplit(".", 1)[-1]) + r"\s*::)",
                        source,
                    ) and Path(path).stem != symbol.rsplit(".", 1)[-1]:
                        dependents.append(_entry(path, relation="source", evidence="Rust module/use declaration", symbol=symbol, line=matched_line))
                    elif not is_test_path(path) and (
                        suffix != ".rs" or Path(path).stem == symbol.rsplit(".", 1)[-1]
                        or bool(re.search(
                            r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:fn|struct|enum|trait|mod)\s+"
                            + re.escape(symbol.rsplit(".", 1)[-1]) + r"\b", source,
                        ))
                    ):
                        # A matched basename is not a Rust module for other
                        # languages. Dart `void main()` can be proven by
                        # current source syntax, but a filename match alone
                        # is merely a candidate, never a referenced call edge.
                        main_name = symbol.rsplit(".", 1)[-1]
                        rust_module = suffix == ".rs" and Path(path).stem == main_name
                        dart_top_level = (
                            suffix == ".dart"
                            and bool(re.search(
                                r"(?m)^(?:(?:Future\s*<\s*void\s*>|void|dynamic|int|String)\s+)"
                                + re.escape(main_name) + r"\s*\(", source,
                            ))
                        )
                        implementations.append(_entry(
                            path,
                            relation="source" if rust_module or dart_top_level else "candidate",
                            symbol=symbol, line=matched_line,
                            evidence=(
                                "Rust module source file" if rust_module
                                else "Dart top-level function declaration" if dart_top_level
                                else "current source text or module name; semantic relation unconfirmed"
                            ),
                        ))
    # Provider-resolved calls/imports and TESTS_FILE paths are preserved. An
    # edge touching a stale source is never presented as a current relation.
    selected_files = {entry["path"] for entry in [*entrypoints, *implementations]}

    def test_reference(path: str) -> tuple[str, int] | None:
        current = python_tree(path)
        direct = _python_test_operation_evidence(current, method="", route=route)
        if direct:
            return direct
        for entry in implementations:
            qualified = str(entry.get("symbol") or "")
            if "." not in qualified:
                continue
            match = _python_test_operation_evidence(
                current, method=qualified.rsplit(".", 1)[-1], route=route,
                implementation_path=entry["path"],
                implementation_class=qualified.split(".", 1)[0],
            )
            if match:
                return match
        return None

    for edge in snapshot.edges:
        if edge.kind not in {"CALLS", "IMPORTS_FILE", "TESTS_FILE"}:
            continue
        source_node = nodes.get(edge.from_id)
        target_node = nodes.get(edge.to_id)
        from_path = symbols.get(edge.from_id, {}).get("path", "") if edge.kind == "CALLS" else str(source_node.identity.get("path") or "") if source_node else ""
        to_path = symbols.get(edge.to_id, {}).get("path", "") if edge.kind == "CALLS" else str(target_node.identity.get("path") or "") if target_node else ""
        if not from_path or not to_path or from_path in stale or to_path in stale:
            continue
        if edge.kind == "CALLS" and edge.to_id in chosen_symbol_ids:
            if is_test_path(from_path):
                tests.append(_entry(from_path, relation="resolved", evidence="CALLS"))
            else:
                dependents.append(_entry(from_path, relation="resolved", evidence="CALLS"))
        if edge.kind == "IMPORTS_FILE" and to_path in selected_files and from_path not in selected_files:
            if is_test_path(from_path):
                if route and from_path.endswith(".py"):
                    reference = test_reference(from_path)
                    if not reference:
                        continue
                    referenced = _entry(from_path, relation="source", evidence="AST-referenced route or imported manager operation", line=reference[1])
                    referenced["reference_kind"] = reference[0]
                    tests.append(referenced)
                else:
                    tests.append(_entry(from_path, relation="resolved", evidence="IMPORTS_FILE"))
            else:
                dependents.append(_entry(from_path, relation="resolved", evidence="IMPORTS_FILE"))
        if edge.kind == "TESTS_FILE" and to_path in selected_files:
            if route and from_path.endswith(".py"):
                reference = test_reference(from_path)
                if not reference:
                    continue
                referenced = _entry(from_path, relation="source", evidence="AST-referenced route or imported manager operation", line=reference[1])
                referenced["reference_kind"] = reference[0]
                tests.append(referenced)
            else:
                tests.append(_entry(from_path, relation="resolved", evidence="TESTS_FILE"))
    # Rust unit tests can live inside the implementation module itself.
    # Verify a real #[test] body references an AST-declared symbol from that
    # module; a similar test name in a different crate is not a test edge.
    rust_crates = {
        tuple(Path(item["path"]).parts[:2])
        for item in implementations
        if item["path"].endswith(".rs") and Path(item["path"]).parts[:1] == ("crates",)
    }
    for implementation in implementations:
        path = implementation["path"]
        if not path.endswith(".rs") or path in stale:
            continue
        reference = _rust_embedded_test_evidence(source_read(path))
        if reference:
            referenced = _entry(path, relation="source",
                              evidence="Rust AST #[test] body references a declaration in this module",
                              symbol=reference[0], line=reference[1])
            referenced["reference_kind"] = "rust_embedded_test_decl"
            tests.append(referenced)
    # Name-only tests in the *same crate* remain candidate navigation. A
    # cross-crate name overlap never establishes a test of this module.
    if symbol and any(item["path"].endswith(".rs") for item in implementations):
        component = symbol.rsplit(".", 1)[-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", component):
            token = re.compile(r"(?<![A-Za-z0-9])" + re.escape(component) + r"(?![A-Za-z0-9])")
            rust_test = re.compile(
                r"(?m)#\[(?:[A-Za-z_][A-Za-z0-9_]*::)?test\]"
                r"\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)"
            )
            for candidate in files:
                if not candidate.endswith(".rs") or candidate in stale:
                    continue
                if Path(candidate).parts[:1] == ("crates",) and tuple(Path(candidate).parts[:2]) not in rust_crates:
                    continue
                content = source_read(candidate)
                for found in rust_test.finditer(content):
                    if not token.search(found.group(1)):
                        continue
                    tests.append(_entry(
                        candidate, relation="candidate",
                        evidence="Rust embedded #[test] name references component; test relation unconfirmed",
                        symbol=found.group(1),
                        line=content.count("\n", 0, found.start()) + 1,
                    ))
                    break

    # A Dart test imports the owning library, which can include another
    # physical file through a referenced `part`/`part of` declaration. The
    # two-hop test -> library -> part path is typed source evidence rather
    # than a similar basename guess; do not claim individual assertions ran.
    dart_part_owners: set[str] = set()
    for edge in snapshot.edges:
        if edge.kind != "IMPORTS_FILE" or edge.facts.get("match_kind") != "dart_part_directive":
            continue
        owner_node, part_node = nodes.get(edge.from_id), nodes.get(edge.to_id)
        owner_path = str(owner_node.identity.get("path") or "") if owner_node else ""
        part_path = str(part_node.identity.get("path") or "") if part_node else ""
        if part_path in selected_files and owner_path and owner_path not in stale and part_path not in stale:
            dart_part_owners.add(owner_path)
    if dart_part_owners:
        for edge in snapshot.edges:
            if edge.kind != "TESTS_FILE":
                continue
            test_node, owner_node = nodes.get(edge.from_id), nodes.get(edge.to_id)
            test_path = str(test_node.identity.get("path") or "") if test_node else ""
            owner_path = str(owner_node.identity.get("path") or "") if owner_node else ""
            if test_path and test_path not in stale and owner_path in dart_part_owners and is_test_path(test_path):
                tests.append(_entry(test_path, relation="resolved", evidence="TESTS_FILE through exact Dart library/part chain"))

    # Name-nearby test modules are navigation candidates, not resolved
    # TESTS_FILE relations (particularly when the snapshot is stale).
    for item in implementations:
        impl_path = item["path"]
        if Path(impl_path).suffix.casefold() == ".dart":
            expected_stem = Path(impl_path).stem + "_test"
            for test_path in files:
                if is_test_path(test_path) and Path(test_path).stem == expected_stem:
                    tests.append(_entry(test_path, relation="candidate", evidence="matching Dart test module name"))
    # Dart imports and library/part paths use the provider's structured
    # relationships above. Stale edges require a rebuild, not a second regex
    # resolver that can mistake comments or same-named paths for imports.
    if route:
        tests.extend(collect_test_references(files, test_reference=test_reference))

    entrypoints, entry_count = _dedupe_entries(entrypoints)
    implementations, implementation_count = _dedupe_entries(
        implementations, preferred_symbol=symbol.rsplit(".", 1)[-1] if symbol else ""
    )
    dependents, dependent_count = _dedupe_entries(dependents)
    tests, test_count = _dedupe_entries(
        tests,
        preferred_symbol=f"{Path(implementations[0]['path']).stem}_test" if len(implementations) == 1 else "",
    )
    return assemble_workflow_result(
        route=route, symbol=symbol, in_file=in_file,
        stages={
            "entrypoints": entrypoints,
            "implementation": implementations,
            "callers_or_dependents": dependents,
            "tests": tests,
        },
        counts={"entrypoints": entry_count, "implementation": implementation_count,
                "callers_or_dependents": dependent_count, "tests": test_count},
        links=links, all_files=files, files_scanned=files_read,
        read_bytes=read_bytes, freshness=freshness,
        skipped_sources=skipped_sources,
    )
