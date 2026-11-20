from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .code_index import CodeIndexEntry, build_code_index, semantic_provider_entries
from .component_projection import (
    DEFAULT_COMPONENT_MANIFEST_REGISTRY,
    ComponentManifestRegistry,
    annotate_graph_query_components,
    component_projection,
    is_current_file_node,
)
from .graph_import_resolver import IMPORT_RESOLVER_LANGUAGES, resolve_code_imports
from .graph_model import GRAPH_QUERY_SELECTOR_SCHEMAS, GraphEdge, GraphNode, GraphQuerySelectorKind, GraphSnapshot, ProviderCoverage, anchor_id, canonical_graph_query_selector, digest_data, document_id, file_id, import_ref_id, knowledge_id, repository_id, symbol_id
from .graph_semantic_model import SemanticProviderResult
from .graph_semantic_provider import build_semantic_providers
from .graph_structured_relations import RpcResolutionOutcome, STRUCTURED_EDGE_KIND, build_structured_file_relations
from .language_profiles import is_semantic_source_language, language_for_path
from .knowledge_records import knowledge_records_for_graph
from .io import Problem
from .path_roles import is_test_path
from .repositories import RepoSelectorResolution, RepoSelectorStatus, RepoTarget, resolve_repo_selector_path


def _has_errors(problems: list[Problem]) -> bool:
    return any(problem.severity == "error" for problem in problems)


def _import_provider_coverage(
    capability: str,
    entries: list[CodeIndexEntry],
    *,
    provider_languages: set[str] | frozenset[str],
    analyzed_paths: list[str],
    evidence_level: str,
) -> ProviderCoverage:
    eligible_entries = [
        entry
        for entry in entries
        if entry.classification != "excluded" and is_semantic_source_language(entry.language)
    ]
    analyzed = set(analyzed_paths)
    supported_entries = [entry for entry in eligible_entries if entry.language in provider_languages]
    return ProviderCoverage(
        capability=capability,
        eligible_paths=tuple(sorted(entry.path for entry in eligible_entries)),
        analyzed_paths=tuple(sorted(entry.path for entry in supported_entries if entry.path in analyzed)),
        unsupported_paths=tuple(sorted(entry.path for entry in eligible_entries if entry.language not in provider_languages)),
        failed_paths=tuple(sorted(entry.path for entry in supported_entries if entry.path not in analyzed)),
        evidence_level=evidence_level,
    )


def _semantic_provider_coverage(
    capability: str,
    entries: list[CodeIndexEntry],
    results: list[SemanticProviderResult],
    *,
    languages: set[str] | None = None,
) -> ProviderCoverage:
    eligible_entries = [
        entry
        for entry in entries
        if entry.classification != "excluded"
        and is_semantic_source_language(entry.language)
        and (languages is None or entry.language in languages)
    ]
    provider_languages = {
        language
        for result in results
        for language in result.languages
    }
    if capability == "symbols":
        analyzed = {
            path
            for result in results
            for path in result.symbol_analyzed_paths
        }
        failed = {
            path
            for result in results
            for path in result.symbol_failed_paths
        }
        capability_evidence = [
            result.symbol_coverage
            for result in results
            if set(result.languages) & {entry.language for entry in eligible_entries}
            and (result.symbol_analyzed_paths or result.symbol_failed_paths)
        ]
    elif capability == "calls":
        analyzed = {
            path
            for result in results
            for path in result.call_analyzed_paths
        }
        failed = {
            path
            for result in results
            for path in result.call_failed_paths
        }
        capability_evidence = [
            result.call_coverage
            for result in results
            if set(result.languages) & {entry.language for entry in eligible_entries}
            and (result.call_analyzed_paths or result.call_failed_paths)
        ]
    else:
        analyzed = {
            path
            for result in results
            for path in result.rpc_analyzed_paths
        }
        failed = {
            path
            for result in results
            for path in result.rpc_failed_paths
        }
        capability_evidence = [
            result.rpc_coverage
            for result in results
            if set(result.languages) & {entry.language for entry in eligible_entries}
            and (result.rpc_analyzed_paths or result.rpc_failed_paths)
        ]
    evidence_level = "conservative" if any(item.evidence_level == "conservative" for item in capability_evidence) else "precise"
    coverage_gaps = tuple(sorted({gap for item in capability_evidence for gap in item.coverage_gaps}))
    supported_entries = [entry for entry in eligible_entries if entry.language in provider_languages]
    failed.update(entry.path for entry in supported_entries if entry.path not in analyzed)
    return ProviderCoverage(
        capability=capability,
        eligible_paths=tuple(sorted(entry.path for entry in eligible_entries)),
        analyzed_paths=tuple(sorted(entry.path for entry in supported_entries if entry.path in analyzed)),
        unsupported_paths=tuple(sorted(entry.path for entry in eligible_entries if entry.language not in provider_languages)),
        failed_paths=tuple(sorted(entry.path for entry in supported_entries if entry.path in failed and entry.path not in analyzed)),
        evidence_level=evidence_level,
        coverage_gaps=coverage_gaps,
    )




def _index_facts(entry: CodeIndexEntry) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "language": entry.language,
        "classification": entry.classification,
        "symbol_names": sorted(entry.symbols),
        "imports": sorted(entry.imports),
        "call_names": sorted(entry.calls),
        "parse_status": entry.parse_status,
        "import_occurrences": [occurrence.to_dict() for occurrence in entry.import_occurrences],
        "module_bindings": list(entry.module_bindings),
        "module_certain_bindings": list(entry.module_certain_bindings),
        "module_wildcard_import": entry.module_wildcard_import,
    }
    if entry.parse_error:
        facts["parse_error"] = entry.parse_error
    return facts


def _file_node(repo_id: str, entry: CodeIndexEntry) -> GraphNode:
    return GraphNode(id=file_id(repo_id, entry.path), kind="file", identity={"repo_id": repo_id, "path": entry.path, "workspace_path": entry.workspace_path}, facts={"index": _index_facts(entry)})


def _materialize_component_manifest(
    node: GraphNode,
    *,
    repository_root: Path,
    registry: ComponentManifestRegistry,
) -> GraphNode:
    manifest_path = str(node.identity.get("path") or "")
    if not manifest_path:
        return node
    provider = registry.provider_for_path(manifest_path)
    if provider is None:
        return node
    try:
        text = (repository_root / manifest_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return node
    manifest_fact = provider.manifest_fact(text)
    if manifest_fact is None:
        return node
    return GraphNode(
        id=node.id,
        kind=node.kind,
        identity=node.identity,
        facts={**node.facts, "component_manifest": manifest_fact},
    )


def _import_declarations(entry: CodeIndexEntry) -> list[dict[str, Any]]:
    if entry.language == "python":
        return [
            {
                "raw_import": occurrence.raw_import,
                "form": occurrence.form,
                "module": occurrence.module,
                "imported_name": occurrence.imported_name,
                "level": occurrence.level,
            }
            for occurrence in entry.import_occurrences
        ]
    return [
        {
            "raw_import": raw_import,
            "form": "raw",
            "module": raw_import,
            "imported_name": "",
            "level": 0,
        }
        for raw_import in entry.imports
    ]


def build_graph(
    root: Path,
    *,
    target: RepoTarget,
    code_index_result: tuple[list[CodeIndexEntry], list[Problem], dict[str, Any]] | None = None,
    cached_semantic_results: list[SemanticProviderResult] | None = None,
    component_manifest_registry: ComponentManifestRegistry = DEFAULT_COMPONENT_MANIFEST_REGISTRY,
) -> tuple[GraphSnapshot | None, list[Problem], dict[str, Any]]:
    entries, index_problems, index_meta = code_index_result or build_code_index(root, changed=False, limit=-1, target=target)
    if _has_errors(index_problems):
        return None, index_problems, {"repository": target.to_dict()}

    summary = index_meta.get("summary", {})
    if summary.get("truncated"):
        return (
            None,
            [
                Problem(
                    "error",
                    "graph_index_truncated",
                    "graph build requires a complete code index; code index output was truncated",
                    target.display_path,
                )
            ],
            {"repository": target.to_dict(), "index": summary},
        )

    problems = list(index_problems)
    repo_id = target.id
    nodes: dict[str, GraphNode] = {
        repository_id(repo_id): GraphNode(
            id=repository_id(repo_id),
            kind="repository",
            identity=target.to_dict(),
        )
    }
    edges: dict[tuple[str, str, str, str, str], GraphEdge] = {}

    def add_edge(edge: GraphEdge) -> None:
        edges[(edge.kind, edge.from_id, edge.to_id, edge.assertion, edge.source)] = edge

    def add_imports(
        entry: CodeIndexEntry, *, provider: str,
        locations: dict[str, list[dict[str, object]]] | None = None,
    ) -> None:
        for declaration in _import_declarations(entry):
            raw_import = str(declaration["raw_import"])
            import_node_id = import_ref_id(
                repo_id,
                entry.path,
                entry.language,
                raw_import,
                form=str(declaration["form"]),
                level=int(declaration["level"]),
                module=str(declaration["module"]),
                imported_name=str(declaration["imported_name"]),
            )
            nodes.setdefault(
                import_node_id,
                GraphNode(
                    id=import_node_id,
                    kind="import_ref",
                    identity={
                        "repo_id": repo_id,
                        "importer_path": entry.path,
                        "language": entry.language,
                        "raw_import": raw_import,
                        "form": declaration["form"],
                        "module": declaration["module"],
                        "imported_name": declaration["imported_name"],
                        "level": declaration["level"],
                    },
                ),
            )
            add_edge(GraphEdge("DECLARES_IMPORT", file_id(repo_id, entry.path), import_node_id, "observed", provider, {"locations": (locations or {}).get(raw_import, [])}))

    for entry in entries:
        file_node = _materialize_component_manifest(
            _file_node(repo_id, entry),
            repository_root=target.root_path,
            registry=component_manifest_registry,
        )
        nodes[file_node.id] = file_node
        add_edge(GraphEdge("CONTAINS", repository_id(repo_id), file_node.id, "observed", "code_index"))

        if entry.language not in {"javascript", "typescript"}:
            add_imports(entry, provider="code_index")

    knowledge_records, knowledge_problems = knowledge_records_for_graph(root, repo_id=repo_id)
    problems.extend(
        Problem("warning", "graph_knowledge_unavailable", problem.message, problem.path, problem.code)
        for problem in knowledge_problems
    )
    current_paths = {entry.path for entry in entries}
    for record in knowledge_records:
        record_id = str(record.get("id") or "")
        if not record_id:
            continue
        record_status = str(record.get("status") or "")
        record_node_id = knowledge_id(record_id)
        nodes[record_node_id] = GraphNode(
            id=record_node_id,
            kind="knowledge",
            identity={"record_id": record_id, "repo_id": repo_id},
            facts={"record": record},
        )
        applies_to = record.get("applies_to", {})
        for raw_path in applies_to.get("paths", []):
            if record_status != "active":
                continue
            resolution = resolve_repo_selector_path(
                raw_path,
                repository_path=target.display_path,
                known_paths=current_paths,
            )
            if resolution.status == RepoSelectorStatus.RESOLVED:
                add_edge(GraphEdge(
                    "KNOWLEDGE_APPLIES_TO", record_node_id,
                    file_id(repo_id, resolution.path), "declared", "knowledge_record",
                ))
            else:
                problems.append(Problem(
                    "warning", "graph_knowledge_path_unresolved",
                    f"Knowledge applicability path is {resolution.status.value}",
                    raw_path,
                ))
        graph_source_refs = record.get("source_refs", [])
        for ref in graph_source_refs:
            if not isinstance(ref, dict):
                continue
            path = str(ref.get("path") or "")
            if not path:
                continue
            source_node_id = document_id(path)
            nodes.setdefault(
                source_node_id,
                GraphNode(
                    id=source_node_id,
                    kind="document",
                    identity={"path": path},
                    facts={"source_ref": ref},
                ),
            )
            add_edge(
                GraphEdge(
                    "KNOWLEDGE_SOURCED_FROM",
                    record_node_id,
                    source_node_id,
                    "recorded",
                    "knowledge_record",
                    {"content_sha256": ref.get("content_sha256")},
                )
            )
    provider_entries = semantic_provider_entries(entries)
    import_resolutions, import_meta = resolve_code_imports(provider_entries, repo=target.root_path)
    semantic_results = build_semantic_providers(
        root,
        target=target,
        entries=provider_entries,
        import_resolutions=import_resolutions,
        cached_results=cached_semantic_results,
    )
    typescript = next((result for result in semantic_results if result.provider == "typescript_compiler"), SemanticProviderResult(provider="typescript_compiler", languages=("javascript", "typescript")))
    import_locations: dict[str, dict[str, list[dict[str, object]]]] = {}
    for declaration in typescript.imports:
        import_locations.setdefault(declaration.path, {}).setdefault(declaration.module, []).append(declaration.anchor.to_dict())
    analyzed_imports = set(typescript.import_analyzed_paths)
    entries = [
        replace(
            entry, imports=sorted(import_locations.get(entry.path, {})),
            parse_status="ok" if entry.path in analyzed_imports else "skipped",
            parse_error="" if entry.path in analyzed_imports else "TypeScript import analysis unavailable",
        )
        if entry.language in {"javascript", "typescript"} and entry.classification != "excluded"
        else entry
        for entry in entries
    ]
    for entry in entries:
        if entry.language not in {"javascript", "typescript"} or entry.classification == "excluded":
            continue
        node = nodes[file_id(repo_id, entry.path)]
        nodes[node.id] = replace(node, facts={**node.facts, "index": _index_facts(entry)})
        if entry.path in analyzed_imports:
            add_imports(entry, provider=typescript.provider, locations=import_locations.get(entry.path))
    import_resolutions, import_meta = resolve_code_imports(semantic_provider_entries(entries), repo=target.root_path)
    dart_semantics = next((result for result in semantic_results if result.provider == "dart_analyzer"), None)
    structured_result = build_structured_file_relations(
        target.root_path,
        entries,
        dart_rpc_invocations=dart_semantics.rpc_invocations if dart_semantics is not None else (),
        dart_rpc_analyzed_paths=dart_semantics.rpc_analyzed_paths if dart_semantics is not None else (),
        dart_rpc_failed_paths=dart_semantics.rpc_failed_paths if dart_semantics is not None else (),
    )
    problems.extend(structured_result.problems)
    for relation in structured_result.relations:
        from_node_id = file_id(repo_id, relation.from_path)
        to_node_id = file_id(repo_id, relation.to_path)
        if from_node_id not in nodes or to_node_id not in nodes:
            continue
        evidence = [item.to_dict() for item in relation.evidence]
        add_edge(
            GraphEdge(
                STRUCTURED_EDGE_KIND,
                from_node_id,
                to_node_id,
                "resolved",
                "structured_file_relations",
                {
                    "evidence_type": "structured_file_relation",
                    "confidence": "medium" if any(item.get("confidence") == "medium" for item in evidence) else "high",
                    "relations": evidence,
                },
            )
        )

    rpc_facts_by_path: dict[str, list[dict[str, object]]] = {}
    for fact in dart_semantics.rpc_invocations if dart_semantics is not None else ():
        rpc_facts_by_path.setdefault(fact.path, []).append(fact.to_dict())
    rpc_resolutions_by_path: dict[str, list[dict[str, object]]] = {}
    for resolution in structured_result.rpc_resolutions:
        rpc_resolutions_by_path.setdefault(resolution.path, []).append(resolution.to_dict())
    for path in sorted(set(rpc_facts_by_path) | set(rpc_resolutions_by_path)):
        node_id = file_id(repo_id, path)
        node = nodes.get(node_id)
        if node is None:
            continue
        resolutions = sorted(rpc_resolutions_by_path.get(path, []), key=lambda item: str(item.get("fact_id") or ""))
        outcome_counts = {
            outcome: sum(1 for resolution in resolutions if resolution.get("outcome") == outcome)
            for outcome in ("linked", "unresolved", "ambiguous", "incomplete")
        }
        nodes[node_id] = GraphNode(
            id=node.id,
            kind=node.kind,
            identity=node.identity,
            facts={
                **node.facts,
                "rpc": {
                    "source_facts": sorted(rpc_facts_by_path.get(path, []), key=lambda item: str(item.get("fact_id") or "")),
                    "resolutions": resolutions,
                    "outcome_counts": outcome_counts,
                },
            },
        )

    precise_symbols = sorted(
        (symbol for result in semantic_results for symbol in result.symbols),
        key=lambda item: (item.provider, item.provider_symbol_id),
    )
    precise_calls = sorted(
        (call for result in semantic_results for call in result.calls),
        key=lambda item: (item.provider, item.caller_provider_symbol_id, item.callee_provider_symbol_id, item.anchor.start_line, item.anchor.start_col),
    )
    direct_test_import_pairs = {
        (resolution.importer_path, resolution.target_path)
        for resolution in import_resolutions
        if is_test_path(resolution.importer_path)
        and not is_test_path(resolution.target_path)
    }
    precise_symbol_node_ids: dict[tuple[str, str], str] = {}
    precise_symbol_paths: dict[tuple[str, str], str] = {}
    for precise_symbol in precise_symbols:
        file_node_id = file_id(repo_id, precise_symbol.path)
        if file_node_id not in nodes:
            continue
        symbol_node_id = symbol_id(repo_id, precise_symbol.provider, precise_symbol.provider_symbol_id)
        anchor = precise_symbol.anchor
        anchor_node_id = anchor_id(repo_id, precise_symbol.provider, anchor.path, anchor.start_line, anchor.start_col, anchor.end_line, anchor.end_col)
        nodes[symbol_node_id] = GraphNode(
            id=symbol_node_id,
            kind="symbol",
            identity={
                "repo_id": repo_id,
                "provider": precise_symbol.provider,
                "provider_symbol_id": precise_symbol.provider_symbol_id,
            },
            facts={
                "provider": {
                    "language": precise_symbol.language,
                    "kind": precise_symbol.kind,
                    "name": precise_symbol.name,
                    "qualified_name": precise_symbol.qualified_name,
                }
            },
        )
        nodes[anchor_node_id] = GraphNode(
            id=anchor_node_id,
            kind="anchor",
            identity={
                "repo_id": repo_id,
                "provider": precise_symbol.provider,
                **anchor.to_dict(),
            },
        )
        add_edge(GraphEdge("DEFINES", file_node_id, symbol_node_id, "resolved", precise_symbol.provider))
        add_edge(GraphEdge("ANCHORS", symbol_node_id, anchor_node_id, "resolved", precise_symbol.provider))
        precise_symbol_node_ids[(precise_symbol.provider, precise_symbol.provider_symbol_id)] = symbol_node_id
        precise_symbol_paths[(precise_symbol.provider, precise_symbol.provider_symbol_id)] = precise_symbol.path

    for precise_call in precise_calls:
        caller_node_id = precise_symbol_node_ids.get((precise_call.provider, precise_call.caller_provider_symbol_id))
        callee_node_id = precise_symbol_node_ids.get((precise_call.provider, precise_call.callee_provider_symbol_id))
        if not caller_node_id or not callee_node_id:
            continue
        add_edge(
            GraphEdge(
                "CALLS",
                caller_node_id,
                callee_node_id,
                "resolved",
                precise_call.provider,
                {"scope": precise_call.scope, "anchor": precise_call.anchor.to_dict()},
            )
        )
        caller_path = precise_symbol_paths.get(
            (precise_call.provider, precise_call.caller_provider_symbol_id),
            "",
        )
        callee_path = precise_symbol_paths.get(
            (precise_call.provider, precise_call.callee_provider_symbol_id),
            "",
        )
        if (
            caller_path
            and callee_path
            and caller_path != callee_path
            and is_test_path(caller_path)
            and not is_test_path(callee_path)
            and (caller_path, callee_path) not in direct_test_import_pairs
        ):
            add_edge(
                GraphEdge(
                    "TESTS_FILE",
                    file_id(repo_id, caller_path),
                    file_id(repo_id, callee_path),
                    "resolved",
                    precise_call.provider,
                    {
                        "evidence_type": "direct_test_call",
                        "confidence": "medium",
                        "call_confidence": "high",
                        "test_role_evidence": "path_role",
                    },
                )
            )

    for resolution in import_resolutions:
        importer_node_id = file_id(repo_id, resolution.importer_path)
        target_node_id = file_id(repo_id, resolution.target_path)
        import_node_id = import_ref_id(
            repo_id,
            resolution.importer_path,
            resolution.language,
            resolution.raw_import,
            form=resolution.form,
            level=resolution.level,
            module=resolution.module,
            imported_name=resolution.imported_name,
        )
        if importer_node_id not in nodes or target_node_id not in nodes or import_node_id not in nodes:
            continue
        add_edge(
            GraphEdge(
                "RESOLVES_TO",
                import_node_id,
                target_node_id,
                "resolved",
                resolution.provider,
                {
                    "importer_path": resolution.importer_path,
                    "match_kind": resolution.match_kind,
                },
            )
        )
        add_edge(
            GraphEdge(
                "IMPORTS_FILE",
                importer_node_id,
                target_node_id,
                "resolved",
                resolution.provider,
                {
                    "raw_import": resolution.raw_import,
                    "match_kind": resolution.match_kind,
                },
            )
        )
        if is_test_path(resolution.importer_path) and not is_test_path(resolution.target_path):
            add_edge(
                GraphEdge(
                    "TESTS_FILE",
                    importer_node_id,
                    target_node_id,
                    "resolved",
                    resolution.provider,
                    {
                        "evidence_type": "direct_test_import",
                        "match_kind": resolution.match_kind,
                        "confidence": "medium",
                        "import_confidence": "high",
                        "test_role_evidence": "path_role",
                    },
                )
            )

    # Exact AST-proven Rust module declarations and Java/Kotlin type imports
    # are source-anchored edges, not lexical file-name coincidences.
    # Provider cache validates this tool payload as part of its source digest.
    for result in semantic_results:
        if result.provider != "tree_sitter_structural":
            continue
        module_relations = result.tool.get("module_file_relations", [])
        for relation in module_relations if isinstance(module_relations, list) else []:
            if not isinstance(relation, dict):
                continue
            from_path = str(relation.get("importer_path") or "")
            to_path = str(relation.get("target_path") or "")
            from_node = file_id(repo_id, from_path)
            to_node = file_id(repo_id, to_path)
            if (not from_path or not to_path or from_path == to_path or
                from_node not in nodes or to_node not in nodes):
                continue
            match_kind = str(relation.get("kind") or "")
            if match_kind not in {
                "rust_mod_declaration", "java_exact_type_import", "kotlin_exact_type_import",
            }:
                continue
            add_edge(GraphEdge(
                "IMPORTS_FILE", from_node, to_node, "resolved", result.provider,
                {"match_kind": match_kind, "anchor": relation.get("anchor", {})},
            ))
            if is_test_path(from_path) and not is_test_path(to_path):
                add_edge(GraphEdge(
                    "TESTS_FILE", from_node, to_node, "resolved", result.provider,
                    {"evidence_type": "exact_structural_import_by_test", "match_kind": match_kind, "confidence": "high"},
                ))

    parse_error_count = int(summary.get("parse_error") or 0)
    provider_coverage = {
        "imports": _import_provider_coverage(
            "imports",
            entries,
            provider_languages=IMPORT_RESOLVER_LANGUAGES,
            analyzed_paths=[str(path) for path in import_meta.get("analyzed_paths", [])],
            evidence_level="conservative",
        ),
        "symbols": _semantic_provider_coverage("symbols", entries, semantic_results),
        "calls": _semantic_provider_coverage("calls", entries, semantic_results),
        "rpc": _semantic_provider_coverage("rpc", entries, semantic_results, languages={"dart"}),
        "structured_relations": ProviderCoverage(
            capability="structured_relations",
            eligible_paths=structured_result.eligible_paths,
            analyzed_paths=structured_result.analyzed_paths,
            unsupported_paths=(),
            failed_paths=structured_result.failed_paths,
            evidence_level="syntax_resolved",
        ),
    }
    source_payloads = {
        "code_index": [entry.to_dict() for entry in entries],
        "knowledge_records": knowledge_records,
        "structured_file_relations": [relation.to_dict() for relation in structured_result.relations],
        "rpc_resolutions": [resolution.to_dict() for resolution in structured_result.rpc_resolutions],
        "python_import_resolver": [resolution.to_dict() for resolution in import_resolutions if resolution.provider == "python_import_resolver"],
        "js_ts_relative_import_resolver": [resolution.to_dict() for resolution in import_resolutions if resolution.provider == "js_ts_relative_import_resolver"],
        "dart_import_resolver": [resolution.to_dict() for resolution in import_resolutions if resolution.provider == "dart_import_resolver"],
    }
    semantic_source_payloads: dict[str, list[dict[str, object]]] = {}
    for result in semantic_results:
        semantic_source_payloads[result.provider] = [symbol.to_dict() for symbol in result.symbols]
        semantic_source_payloads[f"{result.provider}_calls"] = [call.to_dict() for call in result.calls]
        if result.provider == "dart_analyzer":
            semantic_source_payloads[f"{result.provider}_rpc"] = [fact.to_dict() for fact in result.rpc_invocations]
    source_payloads.update(semantic_source_payloads)
    rpc_resolution_status = provider_coverage["rpc"].status
    if rpc_resolution_status == "complete" and any(
        resolution.outcome is RpcResolutionOutcome.INCOMPLETE
        for resolution in structured_result.rpc_resolutions
    ):
        rpc_resolution_status = "partial"
    capability_completeness = {
        "source_inventory": "complete",
        "file_inventory": "complete",
        "imports": provider_coverage["imports"].status,
        "symbols": provider_coverage["symbols"].status,
        "calls": provider_coverage["calls"].status,
        "rpc_resolution": rpc_resolution_status,
        "structured_relations": provider_coverage["structured_relations"].status,
        "knowledge": "partial" if knowledge_problems else "complete",
    }
    overall_completeness = "complete" if all(value == "complete" for value in capability_completeness.values()) else "partial"
    snapshot = GraphSnapshot(
        repository=target.to_dict(),
        sources=[
            {"kind": "code_index", "assertion": "observed", "digest": digest_data(source_payloads["code_index"])},
            {"kind": "knowledge_records", "assertion": "current", "digest": digest_data(source_payloads["knowledge_records"])},
            {"kind": "structured_file_relations", "assertion": "resolved", "digest": digest_data(source_payloads["structured_file_relations"])},
            {"kind": "rpc_resolutions", "assertion": "resolved", "digest": digest_data(source_payloads["rpc_resolutions"])},
            *[
                {"kind": kind, "assertion": "resolved", "digest": digest_data(payload)}
                for kind, payload in sorted(semantic_source_payloads.items())
            ],
            {"kind": "python_import_resolver", "assertion": "resolved", "digest": digest_data(source_payloads["python_import_resolver"])},
            {"kind": "js_ts_relative_import_resolver", "assertion": "resolved", "digest": digest_data(source_payloads["js_ts_relative_import_resolver"])},
            {"kind": "dart_import_resolver", "assertion": "resolved", "digest": digest_data(source_payloads["dart_import_resolver"])},
        ],
        completeness={
            "status": overall_completeness,
            "capabilities": capability_completeness,
            "inventory_complete": True,
            "identity_collisions": 0,
            "index_truncated": False,
            "code_facts_complete": parse_error_count == 0,
            "parse_error_count": parse_error_count,
            "provider_failures": [
                failure.to_dict()
                for result in semantic_results
                for failure in result.failures
            ],
            "provider_coverage": {name: coverage.to_dict() for name, coverage in sorted(provider_coverage.items())},
        },
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        capabilities=["repository", "file", "import_ref", "document", "knowledge", "symbol", "anchor", "import_resolution", "same_file_calls", "cross_file_import_calls", "direct_tests", "structured_file_relations", "rpc_resolution"],
    ).with_digest()
    return snapshot, problems, {
        "repository": target.to_dict(),
        "index": summary,
        "semantic_providers": [result.to_meta() for result in semantic_results],
        "import_resolvers": import_meta,
        "structured_relations": structured_result.to_meta(),
        "provider_coverage": {name: coverage.to_dict() for name, coverage in sorted(provider_coverage.items())},
    }


def _node_by_id(snapshot: GraphSnapshot) -> dict[str, GraphNode]:
    return {node.id: node for node in snapshot.nodes}


def _edge_key(edge: GraphEdge) -> tuple[str, str, str, str, str]:
    return (edge.kind, edge.from_id, edge.to_id, edge.assertion, edge.source)


def _node_summary(node: GraphNode | None, *, symbol_paths: dict[str, str] | None = None) -> dict[str, Any]:
    if node is None:
        return {}
    summary: dict[str, Any] = {"id": node.id, "kind": node.kind}
    if node.kind == "file":
        summary["path"] = node.identity.get("path")
        rpc = node.facts.get("rpc") if isinstance(node.facts.get("rpc"), dict) else {}
        if isinstance(rpc.get("outcome_counts"), dict):
            summary["rpc_outcome_counts"] = rpc["outcome_counts"]
    elif node.kind == "symbol":
        provider = node.facts.get("provider") if isinstance(node.facts.get("provider"), dict) else {}
        summary["name"] = provider.get("name")
        summary["qualified_name"] = provider.get("qualified_name")
        summary["symbol_kind"] = provider.get("kind")
        path = str((symbol_paths or {}).get(node.id) or "")
        if path:
            summary["path"] = path
    elif node.kind == "import_ref":
        summary["raw_import"] = node.identity.get("raw_import")
        summary["language"] = node.identity.get("language")
        summary["importer_path"] = node.identity.get("importer_path")
        summary["form"] = node.identity.get("form")
    elif node.kind == "document":
        summary["path"] = node.identity.get("path")
    elif node.kind == "knowledge":
        record = node.facts.get("record") if isinstance(node.facts.get("record"), dict) else {}
        summary["record_id"] = node.identity.get("record_id")
        summary["knowledge_kind"] = record.get("kind")
        summary["status"] = record.get("status")
        summary["claim"] = record.get("claim")
    return summary






def _symbol_file_id(snapshot: GraphSnapshot, symbol_node_id: str) -> str:
    for edge in snapshot.edges:
        if edge.kind == "DEFINES" and edge.to_id == symbol_node_id:
            return edge.from_id
    return ""


def _symbol_anchor_node(snapshot: GraphSnapshot, symbol_node_id: str) -> GraphNode | None:
    nodes = _node_by_id(snapshot)
    for edge in snapshot.edges:
        if edge.kind == "ANCHORS" and edge.from_id == symbol_node_id:
            return nodes.get(edge.to_id)
    return None


def _symbol_match_dict(snapshot: GraphSnapshot, node: GraphNode) -> dict[str, Any]:
    provider = node.facts.get("provider") if isinstance(node.facts.get("provider"), dict) else {}
    file_node = _node_by_id(snapshot).get(_symbol_file_id(snapshot, node.id))
    anchor_node = _symbol_anchor_node(snapshot, node.id)
    match = {
        "id": node.id,
        "kind": node.kind,
        "name": provider.get("name"),
        "qualified_name": provider.get("qualified_name"),
        "symbol_kind": provider.get("kind"),
        "provider": node.identity.get("provider"),
        "provider_symbol_id": node.identity.get("provider_symbol_id"),
        "path": file_node.identity.get("path") if file_node else None,
    }
    if anchor_node is not None:
        match["range"] = {
            "start_line": anchor_node.identity.get("start_line"),
            "start_col": anchor_node.identity.get("start_col"),
            "end_line": anchor_node.identity.get("end_line"),
            "end_col": anchor_node.identity.get("end_col"),
        }
    return match


def _match_symbols(snapshot: GraphSnapshot, selector: str, *, in_file: str = "") -> list[GraphNode]:
    nodes = _node_by_id(snapshot)
    matches: list[GraphNode] = []
    for node in snapshot.nodes:
        if node.kind != "symbol":
            continue
        provider = node.facts.get("provider") if isinstance(node.facts.get("provider"), dict) else {}
        if selector not in {str(provider.get("name") or ""), str(provider.get("qualified_name") or "")}:
            continue
        if in_file:
            file_node = nodes.get(_symbol_file_id(snapshot, node.id))
            if file_node is None or file_node.identity.get("path") != in_file:
                continue
        matches.append(node)
    return sorted(matches, key=lambda item: (str(_symbol_match_dict(snapshot, item).get("path") or ""), str(_symbol_match_dict(snapshot, item).get("qualified_name") or ""), item.id))


def _query_warnings(snapshot: GraphSnapshot) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if not snapshot.completeness.get("code_facts_complete", True):
        warnings.append(
            {
                "code": "graph_code_facts_incomplete",
                "message": f"code index has {snapshot.completeness.get('parse_error_count', 0)} parse error(s); query results may be incomplete",
            }
        )
    for failure in snapshot.completeness.get("provider_failures", []):
        if isinstance(failure, dict):
            warnings.append(
                {
                    "code": str(failure.get("code") or "graph_provider_failure"),
                    "message": str(failure.get("message") or "semantic provider failed"),
                }
            )
        else:
            warnings.append({"code": "graph_provider_failure", "message": str(failure)})
    coverage = snapshot.completeness.get("provider_coverage")
    if isinstance(coverage, dict):
        incomplete = {
            name: value.get("status")
            for name, value in sorted(coverage.items())
            if isinstance(value, dict) and value.get("status") != "complete"
        }
        if incomplete:
            warnings.append(
                {
                    "code": "graph_provider_coverage",
                    "message": f"semantic provider coverage is incomplete: {incomplete}",
                }
            )
    return warnings


def _path_from_edge(
    nodes: dict[str, GraphNode],
    edge: GraphEdge,
    *,
    reason: str,
    symbol_paths: dict[str, str],
) -> dict[str, Any]:
    source: dict[str, Any] = {"provider": edge.source, "assertion": edge.assertion}
    if edge.facts:
        source["facts"] = edge.facts
    return {
        "from": _node_summary(nodes.get(edge.from_id), symbol_paths=symbol_paths),
        "edge": edge.kind,
        "to": _node_summary(nodes.get(edge.to_id), symbol_paths=symbol_paths),
        "reason": reason,
        "source": source,
    }


def _definition_edges(snapshot: GraphSnapshot, node_ids: set[str]) -> list[GraphEdge]:
    wanted = set(node_ids)
    edges: list[GraphEdge] = []
    for edge in snapshot.edges:
        if edge.kind == "DEFINES" and (edge.from_id in wanted or edge.to_id in wanted):
            wanted.add(edge.from_id)
            wanted.add(edge.to_id)
            edges.append(edge)
        elif edge.kind == "ANCHORS" and edge.from_id in wanted:
            wanted.add(edge.to_id)
            edges.append(edge)
    node_ids.update(wanted)
    return edges


def _file_neighborhood_edges(snapshot: GraphSnapshot, file_node_id: str) -> list[GraphEdge]:
    direct = [edge for edge in snapshot.edges if edge.from_id == file_node_id or edge.to_id == file_node_id]
    symbol_ids = {
        edge.to_id
        for edge in snapshot.edges
        if edge.kind == "DEFINES" and edge.from_id == file_node_id
    }
    call_edges = [
        edge
        for edge in snapshot.edges
        if edge.kind == "CALLS" and (edge.from_id in symbol_ids or edge.to_id in symbol_ids)
    ]
    related_entity_ids = {
        edge.from_id if edge.to_id == file_node_id else edge.to_id
        for edge in direct
        if edge.kind in {"KNOWLEDGE_APPLIES_TO"}
    }
    second_hop = [
        edge
        for edge in snapshot.edges
        if edge.from_id in related_entity_ids or edge.to_id in related_entity_ids
    ]
    definition_ids = {file_node_id, *symbol_ids, *(edge.from_id for edge in call_edges), *(edge.to_id for edge in call_edges)}
    definitions = _definition_edges(snapshot, definition_ids)
    return list({_edge_key(edge): edge for edge in [*direct, *call_edges, *second_hop, *definitions]}.values())


def _file_query_path_edges(snapshot: GraphSnapshot, edges: list[GraphEdge], file_node_id: str) -> list[tuple[GraphEdge, str]]:
    symbol_ids = {
        edge.to_id
        for edge in snapshot.edges
        if edge.kind == "DEFINES" and edge.from_id == file_node_id
    }
    direct_kinds = {"IMPORTS_FILE", "TESTS_FILE", STRUCTURED_EDGE_KIND, "KNOWLEDGE_APPLIES_TO"}
    selected: list[tuple[GraphEdge, str]] = []
    for edge in edges:
        if edge.kind in direct_kinds and file_node_id in {edge.from_id, edge.to_id}:
            selected.append((edge, "direct relation to selected file"))
        elif edge.kind == "CALLS" and (edge.from_id in symbol_ids or edge.to_id in symbol_ids):
            selected.append((edge, "call relation involving a symbol defined by selected file"))
    return selected


def _query_payload(
    snapshot: GraphSnapshot,
    *,
    query: dict[str, Any],
    node_ids: set[str],
    edges: list[GraphEdge],
    matches: list[dict[str, Any]] | None = None,
    paths: list[dict[str, Any]] | None = None,
    warnings: list[dict[str, str]] | None = None,
    candidates: list[dict[str, str]] | None = None,
    query_status: str = "found",
) -> dict[str, Any]:
    nodes = _node_by_id(snapshot)
    for edge in edges:
        node_ids.add(edge.from_id)
        node_ids.add(edge.to_id)
    sorted_edges = sorted({_edge_key(edge): edge for edge in edges}.values(), key=_edge_key)
    return {
        "repository": snapshot.repository,
        "snapshot_digest": snapshot.snapshot_digest,
        "query": canonical_graph_query_selector(query),
        "query_status": query_status,
        "matches": matches or [],
        "candidates": candidates or [],
        "nodes": [nodes[node_id].to_dict() for node_id in sorted(node_ids) if node_id in nodes],
        "edges": [edge.to_dict() for edge in sorted_edges],
        "paths": paths or [],
        "completeness": snapshot.completeness,
        "warnings": warnings if warnings is not None else _query_warnings(snapshot),
    }


def _annotate_query_payload_components(
    payload: dict[str, Any],
    snapshot: GraphSnapshot,
) -> dict[str, Any]:
    return annotate_graph_query_components(payload, component_projection(snapshot))


def _provider_capability_status(
    completeness: dict[str, Any],
    *,
    capability_name: str,
    in_file: str,
) -> str:
    if in_file:
        provider_coverage = completeness.get("provider_coverage")
        provider_coverage = provider_coverage if isinstance(provider_coverage, dict) else {}
        capability_coverage = provider_coverage.get(capability_name)
        capability_coverage = capability_coverage if isinstance(capability_coverage, dict) else {}
        analyzed_paths = {path for path in capability_coverage.get("analyzed_paths", []) if isinstance(path, str)}
        failed_paths = {path for path in capability_coverage.get("failed_paths", []) if isinstance(path, str)}
        if in_file in analyzed_paths:
            return "found"
        if in_file in failed_paths:
            return "unavailable"
        return "unsupported"
    capabilities = completeness.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    status = capabilities.get(capability_name)
    return status if status in {"unsupported", "unavailable"} else "found"






def _empty_query_payload(
    snapshot: GraphSnapshot,
    *,
    query: dict[str, Any],
    query_status: str,
    warning: dict[str, str] | None = None,
    candidates: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    warnings = _query_warnings(snapshot)
    if warning is not None:
        warnings.append(warning)
    return _query_payload(
        snapshot,
        query=query,
        node_ids=set(),
        edges=[],
        warnings=warnings,
        candidates=candidates,
        query_status=query_status,
    )


def _selector_candidates(resolution: RepoSelectorResolution, *, repository_path: str) -> list[dict[str, str]]:
    return [
        {
            "path": path,
            "workspace_path": f"{repository_path}/{path}" if repository_path else path,
            "reason": "selector matches both repo-relative and workspace-relative identities",
        }
        for path in resolution.candidates
    ]


def _canonical_path_candidates(snapshot: GraphSnapshot, requested: str, *, limit: int = 3) -> list[dict[str, str]]:
    requested_name = requested.casefold().rsplit("/", 1)[-1]
    requested_lower = requested.casefold()
    repository_path = str(snapshot.repository.get("path") or "").rstrip("/")
    ranked: list[tuple[tuple[int, int, str], str]] = []
    for node in snapshot.nodes:
        if not is_current_file_node(node):
            continue
        path = str(node.identity.get("path") or "")
        if not path:
            continue
        lowered = path.casefold()
        name = lowered.rsplit("/", 1)[-1]
        same_name = name == requested_name
        suffix_related = lowered.endswith("/" + requested_lower) or requested_lower.endswith("/" + lowered)
        if not same_name and not suffix_related:
            continue
        score = (
            0 if same_name else 1,
            abs(len(path) - len(requested)),
            path,
        )
        ranked.append((score, path))
    return [
        {
            "path": path,
            "workspace_path": f"{repository_path}/{path}" if repository_path else path,
            "reason": "canonical indexed path candidate",
        }
        for _score, path in sorted(ranked)[:limit]
    ]


def _resolve_graph_query_path(
    snapshot: GraphSnapshot,
    *,
    value: str,
    repository_path: str,
    known_paths: set[str],
    query_base: dict[str, Any],
    query_path_field: str,
    invalid_message: str,
    ambiguous_message: str,
) -> tuple[str, dict[str, Any] | None, list[Problem]]:
    resolution = resolve_repo_selector_path(value, repository_path=repository_path, known_paths=known_paths)
    if resolution.status == RepoSelectorStatus.INVALID:
        return "", None, [Problem("error", "graph_query_invalid_path", invalid_message)]

    query = dict(query_base)
    query[query_path_field] = resolution.path if resolution.status == RepoSelectorStatus.NOT_FOUND else value
    if resolution.status == RepoSelectorStatus.AMBIGUOUS:
        return "", _annotate_query_payload_components(
            _empty_query_payload(
                snapshot,
                query=query,
                query_status="ambiguous",
                candidates=_selector_candidates(resolution, repository_path=repository_path),
            ),
            snapshot,
        ), [Problem("error", "graph_query_ambiguous_path", ambiguous_message)]
    if resolution.status == RepoSelectorStatus.NOT_FOUND:
        return "", _annotate_query_payload_components(
            _empty_query_payload(
                snapshot,
                query=query,
                query_status="not_found",
                candidates=_canonical_path_candidates(snapshot, resolution.path),
            ),
            snapshot,
        ), []
    return resolution.path, None, []


def _semantic_query_status(snapshot: GraphSnapshot, *, capability_name: str, in_file: str) -> tuple[str, dict[str, str] | None]:
    provider_coverage = snapshot.completeness.get("provider_coverage")
    provider_coverage = provider_coverage if isinstance(provider_coverage, dict) else {}
    capability_coverage = provider_coverage.get(capability_name)
    capability_coverage = capability_coverage if isinstance(capability_coverage, dict) else {}
    if in_file:
        file_node = _node_by_id(snapshot).get(file_id(str(snapshot.repository.get("id") or ""), in_file))
        index = file_node.facts.get("index") if file_node is not None and isinstance(file_node.facts.get("index"), dict) else {}
        if index.get("classification") == "excluded":
            return "unsupported", {
                "code": "graph_query_unsupported",
                "message": f"source indexing is unavailable for {in_file}",
            }
        status = _provider_capability_status(
            snapshot.completeness,
            capability_name=capability_name,
            in_file=in_file,
        )
        if status == "found":
            return "found", None
        if status == "unavailable":
            return "unavailable", {
                "code": "graph_query_unavailable",
                "message": f"the {capability_name} provider could not analyze {in_file}",
            }
        unsupported_paths = {path for path in capability_coverage.get("unsupported_paths", []) if isinstance(path, str)}
        if in_file in unsupported_paths:
            language = language_for_path(in_file)
            return "unsupported", {
                "code": "graph_query_unsupported",
                "message": f"provider-confirmed {capability_name} queries are unsupported for {language}",
            }
        return "unsupported", {
            "code": "graph_query_unsupported",
            "message": f"{in_file} is not eligible for provider-confirmed {capability_name} queries",
        }
    coverage_status = _provider_capability_status(
        snapshot.completeness,
        capability_name=capability_name,
        in_file="",
    )
    if coverage_status == "unsupported":
        return "unsupported", {
            "code": "graph_query_unsupported",
            "message": f"no provider-confirmed {capability_name} capability is available for the indexed source files",
        }
    if coverage_status == "unavailable":
        return "unavailable", {
            "code": "graph_query_unavailable",
            "message": f"the {capability_name} provider could not analyze the indexed source files",
        }
    return "found", None


def query_graph(
    snapshot: GraphSnapshot,
    *,
    file: str = "",
    import_ref: str = "",
    symbol: str = "",
    callers_of: str = "",
    callees_of: str = "",
    impact_file: str = "",
    impact_symbol: str = "",
    in_file: str = "",
    depth: int = 1,
) -> tuple[dict[str, Any] | None, list[Problem]]:
    selector_inputs = {
        GraphQuerySelectorKind.FILE: file,
        GraphQuerySelectorKind.IMPORT: import_ref,
        GraphQuerySelectorKind.SYMBOL: symbol,
        GraphQuerySelectorKind.CALLERS_OF: callers_of,
        GraphQuerySelectorKind.CALLEES_OF: callees_of,
        GraphQuerySelectorKind.IMPACT_FILE: impact_file,
        GraphQuerySelectorKind.IMPACT_SYMBOL: impact_symbol,
    }
    selectors = [
        (kind, selector_inputs[kind])
        for kind in GRAPH_QUERY_SELECTOR_SCHEMAS
        if selector_inputs[kind]
    ]
    if not selectors:
        return None, [
            Problem(
                "error",
                "graph_query_selector_required",
                "pass exactly one graph query selector: --file, --import, --symbol, --callers-of, --callees-of, --impact-file, or --impact-symbol",
            )
        ]
    if len(selectors) > 1:
        return None, [Problem("error", "graph_query_selector_ambiguous", "pass only one graph query selector")]
    if depth < 1:
        return None, [Problem("error", "graph_query_invalid_depth", "graph query depth must be at least 1")]

    def query_payload(**kwargs: Any) -> dict[str, Any]:
        payload = _query_payload(
            snapshot,
            **kwargs,
        )
        return _annotate_query_payload_components(
            payload,
            snapshot,
        )

    def empty_query_payload(**kwargs: Any) -> dict[str, Any]:
        return _annotate_query_payload_components(
            _empty_query_payload(
                snapshot,
                **kwargs,
            ),
            snapshot,
        )

    repo_id = str(snapshot.repository.get("id") or "")
    repository_path = str(snapshot.repository.get("path") or "")
    selector_kind, value = selectors[0]
    known_file_paths = {
        str(node.identity.get("path") or "")
        for node in snapshot.nodes
        if is_current_file_node(node) and str(node.identity.get("path") or "")
    }
    normalized_in_file = ""
    nodes = _node_by_id(snapshot)
    symbol_paths = {
        edge.to_id: str(nodes[edge.from_id].identity.get("path") or "")
        for edge in snapshot.edges
        if edge.kind == "DEFINES"
        and edge.from_id in nodes
        and str(nodes[edge.from_id].identity.get("path") or "")
    }

    if selector_kind is GraphQuerySelectorKind.FILE:
        normalized, resolution_result, resolution_problems = _resolve_graph_query_path(
            snapshot,
            value=value,
            repository_path=repository_path,
            known_paths=known_file_paths,
            query_base={"type": "file"},
            query_path_field="path",
            invalid_message="graph file query must be a normalized repo-relative path",
            ambiguous_message="graph file query matches both repo-relative and workspace-relative paths",
        )
        if resolution_result is not None or resolution_problems:
            return resolution_result, resolution_problems
        wanted = file_id(repo_id, normalized)
        matched_edges = _file_neighborhood_edges(snapshot, wanted)
        paths = [
            _path_from_edge(nodes, edge, reason=reason, symbol_paths=symbol_paths)
            for edge, reason in _file_query_path_edges(snapshot, sorted(matched_edges, key=_edge_key), wanted)
        ]
        return query_payload(query={"type": "file", "path": normalized}, node_ids={wanted}, edges=matched_edges, matches=[_node_summary(nodes[wanted], symbol_paths=symbol_paths)], paths=paths), []

    if selector_kind is GraphQuerySelectorKind.IMPORT:
        matched_import_nodes = [
            node
            for node in snapshot.nodes
            if node.kind == "import_ref" and str(node.identity.get("raw_import") or "") == value
        ]
        matched_ids = {node.id for node in matched_import_nodes}
        if not matched_ids:
            return empty_query_payload(query={"type": "import", "raw_import": value}, query_status="not_found"), []
        matched_edges = [edge for edge in snapshot.edges if edge.kind in {"DECLARES_IMPORT", "RESOLVES_TO"} and (edge.to_id in matched_ids or edge.from_id in matched_ids)]
        return query_payload(query={"type": "import", "raw_import": value}, node_ids=set(matched_ids), edges=matched_edges, matches=[_node_summary(node, symbol_paths=symbol_paths) for node in matched_import_nodes]), []

    symbol_selector_kinds = {
        GraphQuerySelectorKind.SYMBOL,
        GraphQuerySelectorKind.CALLERS_OF,
        GraphQuerySelectorKind.CALLEES_OF,
        GraphQuerySelectorKind.IMPACT_SYMBOL,
    }
    if selector_kind in symbol_selector_kinds and in_file:
        query_base: dict[str, Any] = {"type": selector_kind.value, "symbol": value}
        if selector_kind is GraphQuerySelectorKind.IMPACT_SYMBOL:
            query_base["depth"] = depth
        normalized_in_file, resolution_result, resolution_problems = _resolve_graph_query_path(
            snapshot,
            value=in_file,
            repository_path=repository_path,
            known_paths=known_file_paths,
            query_base=query_base,
            query_path_field="in_file",
            invalid_message="graph --in-file must be a normalized repo-relative path",
            ambiguous_message="graph --in-file matches both repo-relative and workspace-relative paths",
        )
        if resolution_result is not None or resolution_problems:
            return resolution_result, resolution_problems

    def resolve_one_symbol(
        query_type: GraphQuerySelectorKind,
        raw_symbol: str,
    ) -> tuple[GraphNode | None, dict[str, Any] | None, list[Problem]]:
        matches = _match_symbols(snapshot, raw_symbol, in_file=normalized_in_file)
        query: dict[str, Any] = {"type": query_type.value, "symbol": raw_symbol}
        if query_type is GraphQuerySelectorKind.IMPACT_SYMBOL:
            query["depth"] = depth
        if normalized_in_file:
            query["in_file"] = normalized_in_file
        if not matches:
            return None, empty_query_payload(query=query, query_status="not_found"), []
        match_payloads = [_symbol_match_dict(snapshot, node) for node in matches]
        if len(matches) > 1:
            result = query_payload(query=query, node_ids={node.id for node in matches}, edges=_definition_edges(snapshot, {node.id for node in matches}), matches=match_payloads)
            return None, result, [Problem("error", "graph_query_ambiguous_symbol", f"graph symbol query matched {len(matches)} symbols; pass --in-file or a qualified name")]
        return matches[0], query_payload(query=query, node_ids={matches[0].id}, edges=_definition_edges(snapshot, {matches[0].id}), matches=match_payloads), []

    if selector_kind in symbol_selector_kinds:
        capability_name = "symbols" if selector_kind is GraphQuerySelectorKind.SYMBOL else "calls"
        semantic_status, semantic_warning = _semantic_query_status(snapshot, capability_name=capability_name, in_file=normalized_in_file)
        if semantic_status in {"unsupported", "unavailable"}:
            query = {"type": selector_kind.value, "symbol": value}
            if selector_kind is GraphQuerySelectorKind.IMPACT_SYMBOL:
                query["depth"] = depth
            if normalized_in_file:
                query["in_file"] = normalized_in_file
            return empty_query_payload(query=query, query_status=semantic_status, warning=semantic_warning), []

    if selector_kind is GraphQuerySelectorKind.SYMBOL:
        symbol_node, base_result, problems = resolve_one_symbol(GraphQuerySelectorKind.SYMBOL, value)
        if symbol_node is None:
            return base_result, problems
        call_edges = [edge for edge in snapshot.edges if edge.kind == "CALLS" and (edge.from_id == symbol_node.id or edge.to_id == symbol_node.id)]
        owner_file_id = _symbol_file_id(snapshot, symbol_node.id)
        file_edges = _file_neighborhood_edges(snapshot, owner_file_id) if owner_file_id else []
        edges = list({_edge_key(edge): edge for edge in [*call_edges, *file_edges]}.values())
        path_edges = [(edge, "incoming or outgoing relation for selected symbol") for edge in sorted(call_edges, key=_edge_key)]
        if owner_file_id:
            path_edges.extend(
                (edge, reason)
                for edge, reason in _file_query_path_edges(snapshot, sorted(file_edges, key=_edge_key), owner_file_id)
                if edge.kind != "CALLS"
            )
        paths = [_path_from_edge(nodes, edge, reason=reason, symbol_paths=symbol_paths) for edge, reason in path_edges]
        return query_payload(
            query=base_result["query"] if base_result else {"type": "symbol", "symbol": value},
            node_ids={symbol_node.id},
            edges=edges,
            matches=base_result["matches"] if base_result else [],
            paths=paths,
        ), problems

    if selector_kind in {GraphQuerySelectorKind.CALLERS_OF, GraphQuerySelectorKind.CALLEES_OF}:
        symbol_node, base_result, problems = resolve_one_symbol(selector_kind, value)
        if symbol_node is None:
            return base_result, problems
        if selector_kind is GraphQuerySelectorKind.CALLERS_OF:
            call_edges = [edge for edge in snapshot.edges if edge.kind == "CALLS" and edge.to_id == symbol_node.id]
            reason = "caller invokes selected symbol"
        else:
            call_edges = [edge for edge in snapshot.edges if edge.kind == "CALLS" and edge.from_id == symbol_node.id]
            reason = "selected symbol invokes callee"
        node_ids = {symbol_node.id}
        edges = [*call_edges, *_definition_edges(snapshot, {symbol_node.id, *(edge.from_id for edge in call_edges), *(edge.to_id for edge in call_edges)})]
        paths = [_path_from_edge(nodes, edge, reason=reason, symbol_paths=symbol_paths) for edge in sorted(call_edges, key=_edge_key)]
        return query_payload(
            query=base_result["query"] if base_result else {"type": selector_kind.value, "symbol": value},
            node_ids=node_ids,
            edges=edges,
            matches=base_result["matches"] if base_result else [],
            paths=paths,
            query_status="found",
        ), []

    def impact_walk(
        start_ids: set[str],
        *,
        max_depth: int,
        include_same_file_calls: bool,
    ) -> tuple[set[str], list[GraphEdge], list[dict[str, Any]]]:
        visited = set(start_ids)
        frontier = set(start_ids)
        collected_edges: list[GraphEdge] = []
        collected_edge_keys: set[tuple[str, str, str, str, str]] = set()
        paths: list[dict[str, Any]] = []
        symbol_files = {
            edge.to_id: edge.from_id
            for edge in snapshot.edges
            if edge.kind == "DEFINES"
        }

        def visible_path(edge: GraphEdge) -> bool:
            if edge.kind == "IMPORTS_FILE":
                return True
            if edge.kind == STRUCTURED_EDGE_KIND:
                return True
            if edge.kind != "CALLS":
                return False
            if include_same_file_calls:
                return True
            return symbol_files.get(edge.from_id) != symbol_files.get(edge.to_id)

        for distance in range(1, max_depth + 1):
            next_frontier: set[str] = set()
            for edge in sorted(snapshot.edges, key=_edge_key):
                reason = ""
                if edge.kind == "CALLS" and edge.to_id in frontier:
                    reason = f"depth {distance}: caller depends on changed symbol"
                    next_id = edge.from_id
                elif edge.kind in {"IMPORTS_FILE", "TESTS_FILE", STRUCTURED_EDGE_KIND} and edge.to_id in frontier:
                    relation = "tests" if edge.kind == "TESTS_FILE" else "uses" if edge.kind == STRUCTURED_EDGE_KIND else "imports"
                    reason = f"depth {distance}: file {relation} changed file"
                    next_id = edge.from_id
                elif edge.kind == "DEFINES" and edge.from_id in frontier:
                    reason = f"depth {distance}: file defines potentially changed symbol"
                    next_id = edge.to_id
                elif edge.kind == "DEFINES" and edge.to_id in frontier:
                    reason = f"depth {distance}: symbol belongs to changed file"
                    next_id = edge.from_id
                else:
                    continue
                edge_key = _edge_key(edge)
                if edge_key not in collected_edge_keys:
                    collected_edge_keys.add(edge_key)
                    collected_edges.append(edge)
                    if visible_path(edge):
                        paths.append(_path_from_edge(nodes, edge, reason=reason, symbol_paths=symbol_paths))
                if next_id not in visited:
                    visited.add(next_id)
                    next_frontier.add(next_id)
            frontier = next_frontier
            if not frontier:
                break
        return visited, collected_edges, paths

    if selector_kind is GraphQuerySelectorKind.IMPACT_FILE:
        normalized, resolution_result, resolution_problems = _resolve_graph_query_path(
            snapshot,
            value=value,
            repository_path=repository_path,
            known_paths=known_file_paths,
            query_base={"type": "impact_file", "depth": depth},
            query_path_field="path",
            invalid_message="graph impact-file query must be a normalized repo-relative path",
            ambiguous_message="graph impact-file query matches both repo-relative and workspace-relative paths",
        )
        if resolution_result is not None or resolution_problems:
            return resolution_result, resolution_problems
        wanted = file_id(repo_id, normalized)
        node_ids, edges, paths = impact_walk(
            {wanted},
            max_depth=depth,
            include_same_file_calls=False,
        )
        return query_payload(
            query={"type": "impact_file", "path": normalized, "depth": depth},
            node_ids=node_ids,
            edges=edges,
            matches=[_node_summary(nodes[wanted], symbol_paths=symbol_paths)],
            paths=paths,
        ), []

    symbol_node, base_result, problems = resolve_one_symbol(GraphQuerySelectorKind.IMPACT_SYMBOL, value)
    if symbol_node is None:
        return base_result, problems
    node_ids, edges, paths = impact_walk(
        {symbol_node.id},
        max_depth=depth,
        include_same_file_calls=True,
    )
    return query_payload(
        query=base_result["query"] | {"depth": depth} if base_result else {"type": "impact_symbol", "symbol": value, "depth": depth},
        node_ids=node_ids,
        edges=edges,
        matches=base_result["matches"] if base_result else [],
        paths=paths,
    ), []
