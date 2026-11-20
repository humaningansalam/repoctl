# Source navigation

The source Graph records files, declarations, symbols, and relationships found by language analyzers. Source search retrieves matching text. Task documents and saved Knowledge have their own commands.

## Commands

- `graph build` analyzes the selected repository and updates its derived Graph and text index. `--rebuild` regenerates them from current inputs.
- `graph search QUERY` searches current source and authored documents. Use `--repo-id ID` to select a repository or `--workspace` for workspace documents. `--limit` controls returned files (default 20; -1 returns all matches). `--format text|markdown` selects a readable rendering; `--json` returns structured results.
- `graph query` reads the stored Graph using a file, import, symbol, caller, callee, or impact selector. It returns the matching nodes, edges, paths, and diagnostics. Impact traversal uses the requested depth.
- `graph workflow` locates a route or symbol implementation and its observed relationships and tests.

Use each command's `--help` for selectors. Select the repository explicitly when the workspace contains several.

## Text search

Each result identifies the actual file, source kind, line range, snippet, and SHA-256 of its bytes. Matching uses Unicode words and identifier boundaries. Exact path and filename matches sort first; other matches use BM25 with the reported parameters. The result includes matched terms and score. File kinds receive the same scoring.

Search reports total matches, returned count, truncation, files searched, and skipped files. Non-UTF-8 and binary files are outside the text corpus. Unreadable sources or files that change during a read produce partial coverage.

The corpus consists of included repository files and workspace documentation. Task directories, archived Tasks, the Task board, Knowledge records, and derived runtime state are separate stores. Their contents do not enter generic source ranking. Explicit Knowledge is available through `knowledge query/show`; work records through `task list/show/resume`.

Search is read-only. It can scan current files without a saved index. When an index exists, unchanged files use cached text and changed or new files are read directly; deleted files are excluded. Both paths use the same corpus statistics and scoring. A missing or unreadable text index does not prevent live search or reading stored Graph relations.

## Relationship meaning

A resolved edge has supporting syntax or analyzer evidence. Unresolved and ambiguous relations retain the analyzer's candidates and reasons. Dynamic calls do not establish a target unless the provider resolves it. Each provider reports the files it analyzed, failed to analyze, or does not support.

Graph queries report snapshot freshness. A stale snapshot still describes the earlier inputs; refresh it explicitly to analyze current source. Read failures are reported as unavailable inputs and never converted into a content hash.

Workflow results report files and bytes actually read. Unreadable, malformed, or concurrently changed sources appear in `completeness.skipped_sources` with their reasons. Partial analysis cannot establish that a requested implementation is absent.

Saved Knowledge may declare applicability to a path and may cite source files. These are different relationships: `KNOWLEDGE_APPLIES_TO` uses authored applicability paths; `KNOWLEDGE_SOURCED_FROM` records citations and their recorded hashes. Citing a file does not declare applicability or turn a work record into Knowledge.

## Derived storage and providers

Snapshots and indexes can be rebuilt. They do not determine Task status or completion. Language analyzers inspect source; they do not run the product's build or tests.

The TypeScript provider can use a project compiler or the bundled compiler. The bundled compiler's Apache 2.0 license accompanies it.
