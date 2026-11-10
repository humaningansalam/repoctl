# repoctl v0.16.2 — precise sibling attribution and Chosen overlap warnings

Prepared: 2026-11-10 (KST). Independent patch release. The agent-handoff-template document contract remains **1.0.0**; no template release or embedded runtime migration is required.

- Adds exact committed-path attribution with `--acknowledge-completed TASK_ID:PATH` for a done sibling sharing the current product Task's parent and stable repository. The complete original receipt is verified before the selected paths are projected in memory.
- Rejects selections overlapping current Chosen, invalid or missing paths, pending worktree edits, missing transition evidence, rewritten history, and prior/later changes that break committed lineage. Rename selection must include both old and new endpoints; either endpoint's pending edit rejects the selection. Whole-task and path-scoped selectors cannot be mixed.
- Doctor and finish expose selected paths with the original receipt path/hash. Finish records the attribution without claiming those mutations as current-Task changes, rewriting completed Task/receipt/catalogue history, changing current Chosen/baseline/ownership, or modifying product Git history. Existing root-only whole-task acknowledgement and strict closure checks remain intact.
- Allows root-only parents to compose verified committed child spans across a later parent start and nested observations. Original receipt endpoints and the parent's recorded path state must match their Git anchors; prefix projection is in memory and does not transfer ownership. Uncovered path mutations remain blocking even after an edit is restored. Product-task acknowledgement and working-tree lineage retain their existing checks; immutable authority and product history are preserved.
- Preserves completion catalogue authority when normal parent closure archives completed children. Readers accept an unchanged completion-time live path only through the validated exact-ID archive locator and matching immutable receipt/artifact hashes. Pending and ingested history use the same proof; no receipt or event rewrite, catalogue rebuild, or historical path replacement is required.
- Adds read-only `task_chosen_overlap` warnings to Discovery append/replace, exact or selected single-live resume, and doctor. Same-repository live Tasks report deterministic bounded path intersections; different-repository, root-only, and terminal Tasks are excluded. Ambiguous resume still requires exact selection.
- Overlap is advisory: it does not block a successful Discovery update or an otherwise ready finish, invalidate Handoff, transfer ownership, rewrite scope, lock work, or schedule agents. Product acceptance remains separate from Task closure.
- Corrects Handoff audit false positives from prospective constraints, product ACK terminology, commands and patch names. Operational-history warnings now require explicit state records or timestamped protocol events outside inert code/command values; genuine embedded records still warn. Audit remains read-only and advisory, and the separate 50 KiB source-size diagnostic is unchanged.
- The existing v0.16.1 runtime remains available for rollback. Split adopters install v0.16.2 in a separate versioned directory and update their thin launcher and current operating guidance after public-asset verification; historical Task, receipt, and release evidence stays unchanged.

---

## Previous release: repoctl v0.16.1

Released: 2026-11-07 (KST)

Immutable patch to the independent repoctl v0.16.0 runtime. The document-authoring template remains v1.0.0; no new template release is required.

- C1: Context Docs now observes bare/backtick/Markdown/reference links, task-relative paths, numbered/star lists and fragments. Fenced examples and HTML comments are excluded. Default Task resume exposes bounded observation coverage and makes required/unobserved references inactive instead of silently executable.
- C2: Upgrade plan and apply deny split document workspaces and thin external launchers before any managed write or debug event. Legacy embedded targets retain separately covered behavior.
- C3: The regular launcher picks its own installed .venv/bin/python, including the optional structural extra. Explicit PYTHON overrides are honored and invalid overrides fail. No product virtualenv is silently selected and no read installs dependencies.
- C4: Runtime README documents versioned external installation, normal wrapper invocation, direct upgrade, and source contract paths. Adopter current docs are reconciled separately without editing historical Task receipts or product Git.
- C5: The independent source repository tracks the recovered general regression suite, deterministic harness and test-only pinned template/benchmark authority fixtures. CI and release publication must run the full functionality and conformance suite; packaged user assets remain runtime-only.

Install:

    cd /absolute/path/to/repoctl-0.16.1
    uv sync --locked --extra structural
    REPOCTL_WORKSPACE_ROOT=/absolute/path/to/adopter ./scripts/repoctl --version

A split adopter installs this in a new directory and updates only its thin runtime pointer and current operating guidance, after preserving old bytes for rollback. Do not run legacy embedded upgrade against the split workspace.

Unchanged boundaries: repoctl is source-grounded navigation and lifecycle support, not a universal compiler, agent orchestrator, automatic editor, or performance guarantee. Graph uncertainty remains partial/candidate/unsupported when evidence is insufficient.

Source provenance: independent v0.16.0 tag (f2e0e63b3ab8e91e1c0cc39ac74a3d552b35ea48); original combined source v0.15.3 base (3b0780fc1366e2171eca1075cb664be80c71b75f). The pinned template v1.0.0 source is d7f7675b26a4583c23f4a4f39448d38454cc534e.
