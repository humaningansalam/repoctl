# repoctl 0.16.1 — independent runtime

This release implements Task lifecycle and Handoff freshness, Context Docs observation, Graph/Knowledge evidence navigation, machine JSON contracts, and optional structural code providers. The separate [agent-handoff-template v1.0.0](https://github.com/humaningansalam/agent-handoff-template/releases/tag/v1.0.0) owns human authoring documents. No Task/Parent Task template, Board, PRD scaffold, model selection, agent hierarchy or agent orchestration is installed or selected by repoctl.

## Install and run

Install the published runtime in a separate versioned directory. From that directory:

~~~sh
uv sync --locked --extra structural
./scripts/repoctl --version
REPOCTL_WORKSPACE_ROOT=/absolute/path/to/adopter ./scripts/repoctl task resume --json
~~~

The ordinary launcher uses **the runtime's** .venv/bin/python when that environment exists, not the product workspace's Python or VIRTUAL_ENV. An explicit PYTHON override wins and an invalid override fails. Without an installed runtime environment, core-only execution falls back to system python3 and reports structural-provider limitations honestly. Read/query commands never auto-install packages.

An adopter may keep a thin user-owned scripts/repoctl wrapper that exports its exact workspace root and invokes the external versioned runtime. Runtime Python source is not copied into adopter workspaces. Protect existing authoring docs, Task/Knowledge/receipts, local policies, and product Git.

## Context Docs supported syntax and coverage

In a Task's Context Docs section, repoctl observes explicit bullet entries using -, * or numbered list syntax, with optional Requirement:/Supporting: roles. Entries can name a workspace-relative docs/... or repos/... path, a backtick path, [name](path) Markdown link or [name][ref] with [ref]: path, including #fragments. /docs/... is workspace-rooted; ../specs/... is relative to the Task file's directory; legacy docs/... links remain workspace-relative unless a conflicting task-relative file makes the destination ambiguous. No basename guessing. Fences and HTML comments are examples, not references. The digest covers the whole file even when a fragment is named.

Observations are bounded to 32 references of at most 1 MiB each. Default task resume reports reference_count, observed_count, unobserved_count, per-status issues, and truncation. Missing, invalid, unreadable, oversized, ambiguous, unsupported and external-URL references are disclosed; URLs are not fetched. If a required reference is unobserved (or a reference-budget overflow hides its role), the Handoff is inactive/non-executable until evidence is reviewed. Supporting/unclassified issues remain visible without automatically requiring approval. Existing bound digests are not silently rewritten.

## Upgrade boundary and development

Embedded-runtime upgrade plan/apply remains limited to genuine legacy embedded-runtime targets. Split document workspaces and external thin launchers reject such upgrades without modifying runtime, Task, Board, Knowledge, receipts, or debug journal. For split adopters, install a new external versioned runtime and adjust the wrapper directly; the old version remains for rollback.

Independent source development runs the full regression and template conformance suite:

~~~sh
uv sync --locked --extra structural
uv run --locked --extra structural pytest -q
~~~

Development tests, benchmark fixture copies and source-only CI do not enter the published runtime archive. Source: https://github.com/humaningansalam/repoctl. Release identity: repoctl 0.16.1.
