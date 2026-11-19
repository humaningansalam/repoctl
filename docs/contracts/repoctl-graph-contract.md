# Graph source navigation

Graph is a disposable view of project source and declared references. Source files and repository configuration are the underlying record.

## Operations

- `graph build` analyzes the selected repository and updates derived Graph state.
- `graph search` finds source-linked material relevant to a question.
- `graph query` and `graph workflow` follow recorded relationships.

Use each command's `--help` for selectors and output options. Select the repository explicitly when the workspace contains several.

## Meaning of results

A resolved relationship has supporting syntax or analyzer evidence. A lexical match is a candidate. Results retain this distinction and link to the file or symbol that supports them. Dynamic attribute calls remain candidates unless a provider resolves the target; application-factory assignments do not establish that relationship.

Workflow results report the files and bytes actually read. Unreadable, oversized, malformed, or budget-skipped sources appear in `completeness.skipped_sources` with a path and reason, and make the search partial. An incomplete search cannot establish `not_found`.

Language analyzers have different coverage. Missing analyzers and unsupported or ambiguous constructs produce partial or unavailable results. They do not prove that there is no impact. Current source can still be inspected directly.

A stale snapshot describes earlier source. Queries disclose freshness and exclude relationships that no longer have current supporting inputs. Refresh analysis explicitly when needed.

## Storage

Snapshots and indexes can be rebuilt from their sources. They do not determine Task status, completion, or approval. Querying a source does not register a workflow step or create a persistent query receipt.

Knowledge supplies explicit saved decisions and source references. A source change may require reconsidering a decision; it does not rewrite saved reasoning.

## Providers

Core navigation remains usable without every optional analyzer. Structural providers describe source relationships; they do not run the product's build or test suite.

The bundled TypeScript compiler is a fallback for TypeScript/JavaScript analysis when an existing compiler is unavailable. Its Apache 2.0 license accompanies the compiler in the runtime artifact.
