# Task Requirements

A Task contains current work only: Goal, actual Chosen scope, current decisions/constraints, meaningful result or blocker, next action/done condition, and source-document references. Bulk output, dirty/residue arrays, fixtures, copied inventories, role/request/ACK state, and repeated history do not belong in it.

Lifecycle commands keep full machine detail in the immediate structured command output while Markdown records bounded counts. No fake persistent detail reference is invented. Chosen replacement logs record reason and added/removed counts; Discovery contains the current list once.

Handoff has exactly four restart fields. Freshness observes the Task contract, Discovery scope/Notes, repository observation, child lifecycle, and Handoff. Execution Log and optional Verification prose are not freshness inputs. A changed next action must be represented in the current contract, Notes/constraints, or Handoff.

The current v7 Handoff binding records content digests for explicitly referenced Context Docs (both requirements and supporting references), including a missing/unreadable status. A changed, removed, or renamed referenced file makes the bound Handoff inactive and exposes path-specific review evidence in resume. Unrelated file changes do not count as Context Docs drift. Earlier v6 and older bindings require deliberate rebind rather than silent trust.

`task audit` is read-only and warning-only. `task migrate-slop` is dry-run by default and applies only recognized generated-line rewrites to live Tasks. Default apply never changes closed `done`/`canceled` Tasks, even if a child still lives under `docs/tasks/`. A single explicitly selected closed Task can be cleaned with `--closed`, after checking the original completion receipt and saving an original-byte migration witness without rewriting history as new verification. Atomic source writes and validated rollback preserve the original on failure.

Semantic workslop lint is advisory only. It recognizes dense repeated top-level Execution Log checkpoints after normalization of timestamps and numeric tokens, reports a representative source line and a bounded evidence count, and ignores fenced examples. It never rewrites the Task or treats long, distinct reasoning, incident timelines or multilingual content as repetitive solely for length. A warning is not an assertion of task failure.

Handoff operational-history warnings require explicit protocol-state records (such as `ACK:` or `collector:`) or timestamped protocol events. Ordinary restart constraints and product terminology do not qualify by keyword alone. Inline/fenced code and the First command value are inert source; other restart fields remain auditable for embedded records. The separate diagnostic source-size threshold remains 50 KiB, including for distinct authored reasoning, and never changes Task authority or lifecycle status.

A current v5 completion receipt may explicitly mark `historical_transition_evidence.status=not_recorded` only when the one-time converter preserved a byte-identical original receipt and a verifiable conversion witness. This represents an old completion without originally recorded start/transition events; it cannot supply descendant path lineage or count as newly verified historical timing. The original Task content hash and actual recorded results remain unchanged. Pre-existing Task drift and genuinely inconsistent partial evidence are separate blockers.

An exact old receipt cited by Knowledge requires finalizing any active v1 first. The optional one-ID `task migrate-receipts --relink-knowledge` transition relocates SHA-verified current v2 source citations to the byte-identical archived original, preserving authored claims, reasons and timestamps; it rejects mismatched digests, dependent citations and concurrent changes. It does not add a legacy reader to normal execution.

## Root-only committed observation composition

- repoctl shall allow a root-only Task to compose completed descendants' committed observations and exact whole-task acknowledgements only after validating the original receipts, their before/after states against recorded Git revisions, and one ordered ancestry through the current terminal revision.
- repoctl shall require the parent's recorded path state to match its Git start anchor before projecting committed observations. It shall not discard or reinterpret an inherited dirty overlay to make closure pass.
- repoctl shall project pre-parent prefixes and nested committed spans only in memory. A historical prefix shall not become a new parent-range mutation or transfer product ownership.
- repoctl shall reject invalid execution intervals, contradictory completion order, endpoint mismatches, incomparable histories, and uncovered committed mutations on receipt-claimed paths before, between, or after covering spans. Later restoration of identical endpoint bytes shall not hide a mutation in an uncovered interval.
- repoctl shall preserve original Task artifacts, receipts, catalogue events, baselines, ownership decisions, and product history. It shall preserve product-task whole/path acknowledgement and working-tree-only lineage behavior and shall not turn unmatched committed observations into root-owned product changes.

## Completed sibling path attribution

- repoctl shall allow a repo-scoped product Task to acknowledge an exact committed path from a distinct done sibling only when both Tasks share the same non-empty parent and stable repository target, the complete immutable completion receipt is valid, and the current committed lineage verifies the selected mutation.
- repoctl shall require each canonical repo-relative selected path to occur in the sibling receipt's changed entries and verified path transitions. It shall retain the recorded current-Task baseline and require history descending from the recorded start HEAD.
- repoctl shall reject selected paths overlapping current Chosen, pending worktree edits, missing transition evidence, incomplete rename edges, and prior or later changes that break the selected sibling lineage. A complete old/new rename selection may be acknowledged only when both endpoints satisfy the checks.
- repoctl shall reject whole-task and path-scoped selectors mixed in one invocation, including selections of different Task IDs. Existing root-only whole-task acknowledgement and a valid disjoint whole-sibling claim retain their separate semantics.
- repoctl shall expose the original receipt path/hash and canonical, sorted, unique selected paths in successful doctor/finish output and record the selection in the current Task's completion log. It shall not claim selected sibling mutations in the current Task's completion changed entries.
- repoctl shall not rewrite the sibling Task, receipt, completion catalogue event, product Git history, current Chosen, task-start baseline, or existing ownership decisions. Task closure shall remain separate from product acceptance.

## Live Chosen overlap warnings

- repoctl shall report `task_chosen_overlap` when distinct live Tasks (`todo`, `doing`, or `blocked`) in exactly the same stable repository have a non-empty intersection of canonical Chosen paths, regardless of parentage.
- repoctl shall evaluate Discovery's complete resulting Chosen set after append or replace, exact and selected single-live resume, and doctor through the same read-only comparison. It shall preserve ambiguous bare resume without choosing a candidate.
- repoctl shall exclude root-only, different-repository, done, canceled, and archived Tasks. It shall not turn invalid Chosen values into overlap claims or replace existing validation errors.
- repoctl shall report current/other Task IDs, other status, repository ID, deterministic bounded paths, complete overlap counts, and truncation. Task records shall be sorted by other Task ID and paths lexically.
- repoctl shall allow Discovery to succeed and an otherwise ready Task to remain finish-ready when overlap is the only finding. The warning shall not change Handoff freshness or lifecycle health.
- repoctl shall not mutate Board, Task state, receipts, ownership, dependencies, product files, or the Git index while evaluating overlap. It shall not lock, schedule, split, reopen, or automatically rescope Tasks. Discovery's requested authoring write shall retain its atomic boundary.
