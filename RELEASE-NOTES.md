# repoctl v0.16.0

Released: 2026-11-06 (KST)

First independent runtime release following the combined v0.15.3 baseline.

- Accepts explicit external workspace roots through REPOCTL_WORKSPACE_ROOT.
- Supports template document contract 1.x and fails closed on unsupported major versions before Task/Board writes.
- Includes Task lifecycle and Handoff freshness, Context Docs observation, Graph/Knowledge with source provenance, and machine-readable JSON contracts.
- Preserves optional structural Graph providers through the `structural` dependency extra.
- Separates warning-only AI workslop diagnostics from required actions and acceptance evidence.
- Excludes human Task/Parent Task/Board/PRD authoring scaffolds.

Graph may honestly report partial/candidate/unsupported results when evidence is insufficient. No claim is made of universal natural-language first-file accuracy, compiler-level dispatch for every language, or measured model token/patch improvement.

Adopter rollout is explicit and sequential. Existing authored documents, Task history, completion receipts, Knowledge, product Git identities and dirty files must be preserved.

Source provenance: separation implementation based on combined `v0.15.3` at `3b0780fc1366e2171eca1075cb664be80c71b75f`.
