# repoctl v0.16.1

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
