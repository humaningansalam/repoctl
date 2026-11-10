# repoctl Task Scope Contract

## Purpose

The current model keeps one scope authority: the Task's `## Discovery` section.

```markdown
## Discovery

- Chosen files:
  - `repos/src/owner.py`
  - `repos/tests/test_owner.py`
- Notes:
  - owner and direct behavior check move together
```

Chosen records intended product files. Notes keep only a scope decision or source reference useful to the next reader. Search history, opened files, candidate lists, exclusions, result selections, and check results are not current Discovery state.

## Mutation

Use:

```bash
./scripts/repoctl task discovery add T-... --chosen repos/path --json
./scripts/repoctl task discovery add T-... \
  --replace-chosen repos/new-path \
  --reason "scope moved to the actual owner" \
  --json
```

`--chosen` appends and deduplicates. `--replace-chosen` replaces the complete set and requires `--reason`; the reason is appended to Execution Log. `--note` adds a deduplicated free-form note. A call must provide at least one of those inputs.

Every Chosen value must be a canonical workspace-relative file. Repo-scoped Tasks must keep values inside their selected repository. Directories, absolute paths, traversal, backslashes, and cross-repository paths fail closed.

A `todo` Task may record Chosen before start. A `doing` or `blocked` Task may change it only when current task-start state exists for the same immutable repository selection. Done and canceled Tasks remain immutable; later scope belongs in a follow-up Task.

## Live Chosen overlap warnings

Two live product Tasks in the same stable repository may have overlapping Chosen files, regardless of parentage. `task discovery add`, exact `task resume <TASK_ID>`, selected single-live resume, and `task doctor` expose a read-only `task_chosen_overlap` warning for each other live Task with a non-empty canonical path intersection. Live means `todo`, `doing`, or `blocked`; self, done, canceled, archived, root-only, and different-repository Tasks do not contribute a warning. An identical relative path in another repository is not a conflict.

Discovery compares the complete resulting Chosen set: append compares the existing set plus additions, while replace compares only the replacement set. It preserves the existing atomic write boundary and succeeds when overlap is its only finding. Resume also evaluates author-written Task Markdown, without requiring a prior Discovery command. Ambiguous bare resume keeps its candidate-selection result and does not choose a Task.

Repository identity and canonical path validation remain authoritative. Invalid Chosen values do not become overlap claims. The warning reports the current Task, other Task and status, stable repository ID, and bounded repo-relative overlapping paths, sorted by other Task ID and then path. Full path counts and truncation are explicit in the [JSON contract](repoctl-json-contract.md).

Warning evaluation does not write Task, Board, state, receipts, ownership, dependencies, product files, or the Git index. It neither locks nor schedules work, rewrites scope, splits Tasks, transfers ownership, reopens completed work, or makes Handoff stale. Overlap alone does not block Discovery or make an otherwise ready Task fail doctor/finish. Actual changes outside Chosen remain a separate closure error.

## Finish relationship

For product work, doctor and finish compare actual task changes with Chosen:

- actual paths outside Chosen are hard closure problems
- Chosen paths without actual changes are reported for inspection and do not invent changes
- pre-existing dirty paths remain governed by baseline ownership
- missing or placeholder Chosen blocks a repo Task that has product changes

Chosen describes scope. It is not proof that a file was read, reviewed, tested, or correct. `## Verification` is separate optional prose and does not participate in Discovery or finish readiness.

## Graph and source discovery

Graph search and query, Git, metadata queries, Knowledge, and direct source reads help an agent choose files. Their output stays read-only. Querying them creates no Task-owned result receipt, episode, candidate disposition, or future ranking signal.

Graph search and Knowledge can cite current Task files without creating or redefining Discovery scope.

Fields from retired Discovery workflows and files under `docs/tasks/.repoctl-state/discovery-outcomes/` are ignored. On the next `task discovery add`, the Task section is rewritten with Chosen and Notes only; Goal, Execution Log, Verification prose, and other Task history stay intact. An older Handoff binding is inactive until the agent reviews and binds the current Task once.

## Completion history

Task finish publishes one immutable v5 completion receipt and bounded history entry. New history derives changed paths and searchable human Task text without freezing a Discovery episode or structured check record. Explicit Graph history search and task/artifact selectors may inspect that history; ordinary current-source ranking does not consume it as scope or authority. Historical receipt schema translations are explicit one-time conversions, not normal legacy reads.

Completion receipts and Task artifacts preserve history identity. They make no claim that a command or test ran.

### Root-only committed child observations

A root-only Task may compose completed descendants' committed observations even when a child started before the parent's recorded baseline or its observation overlaps other completed children. Each original receipt remains fully validated. For every receipt-claimed path, its before and after states must match the immutable Git trees at the receipt's recorded start and observed revisions. Child, parent, and current revisions must form one ordered ancestry, with valid execution intervals and consistent completion order.

The parent's recorded starting path state and current terminal state must also match their Git anchors. A pre-existing dirty overlay is never silently replaced by the start-tree state. Verified child spans are clipped to the parent's recorded interval only in memory; observations ending before the parent start remain historical evidence and do not claim parent-range mutations. Nested or overlapping spans may confirm the same observed history without applying an edit twice.

For a receipt-claimed path, every committed mutation between the parent start and the first covering span, between covering spans, or after the last span remains a closure error. This check observes individual commits, so an unrelated edit followed by restoration to identical bytes is still detected. Unrelated committed paths continue to be observations under the existing root-only contract; composition does not assign product ownership to the root Task.

This behavior is limited to root-only committed closure, including explicitly acknowledged completed Tasks below. Product whole-sibling/path-scoped attribution and working-tree-only lineage retain their own checks. Projection never rewrites Task artifacts, original receipts or catalogue history, recorded baselines, ownership decisions, or product Git history. Task closure and product acceptance remain separate.

### Root-only whole-task acknowledgement

For root-only closure blocked by changes already owned by an archived standalone
product Task, use `task doctor T-root --acknowledge-completed T-product --json`,
then `task finish` with the same option. Repeat the option only for the exact
completed Tasks being acknowledged. This reuses receipt integrity, repository
identity and baseline-to-current path lineage checks; additional drift remains
blocking. It does not change repository selection, parentage or product ownership.
Finish records the acknowledged receipt path/hash in the root Task log without
rewriting the completed Task or receipt. Product acceptance remains separate.

### Product sibling path attribution

A product Task may acknowledge exact committed paths from a done sibling without adopting that sibling's entire receipt:

```bash
./scripts/repoctl task doctor T-current \
  --use-committed-diff \
  --acknowledge-completed T-sibling:docs/RELEASE.md \
  --json
```

After reviewing a successful doctor result, pass the same options to `task finish`. Task IDs in examples stand for exact existing IDs. The path after `:` is a canonical **repo-relative file path**, unlike workspace-relative Chosen entries.

This is a separate contract from root-only whole-task acknowledgement. Both product Tasks must resolve to exactly the same stable repository target and share a non-empty parent; the other Task must be done and distinct from the current Task. A bare sibling ID still requests its entire receipt and is valid only when the whole claim satisfies the existing checks, including disjointness from current Chosen. Whole-task and path-scoped selectors cannot be mixed in one invocation, even across different Task IDs.

The complete immutable completion receipt is validated before selected entries are projected in memory. Each selected path must occur in both its `changed_entries` mutation paths and verified `repo_evidence.path_transitions`. The current Task must have its recorded baseline, and the repository history must descend from its start HEAD. The selected transition must be verified within that start-to-terminal committed range; a matching path name or terminal file content alone is insufficient.

Selection fails closed for an invalid, missing, absolute, or traversal path; a missing or damaged receipt, identity, hash, or transition; another parent/repository; a live sibling; or a selected path overlapping current Chosen. It also rejects pending worktree edits on any selected mutation path, missing baseline, rewritten history, absent committed transition, a prior current-Task edit that breaks the sibling lineage, and a later unrelated commit that changes the selected path after sibling completion.

A rename is one mutation edge. Select both the old and new paths with repeated `--acknowledge-completed T-sibling:PATH` options; either half alone is rejected. A pending edit on either endpoint rejects the full-edge selection too.

Successful doctor/finish output records `acknowledged_completions` with the sibling Task ID, original receipt path and SHA-256, and canonical, sorted, unique `selected_paths`. Finish appends that selection and original receipt identity to the current Task's Execution Log. It excludes the acknowledged mutation from the current Task's completion `changed_entries`.

This operation does not rewrite the sibling Task Markdown, original receipt bytes, original completion catalogue event, product Git history, current Chosen, recorded task-start baseline, or existing ownership decisions. An inherited dirty overlay restored to the recorded start tree does not become a committed claim merely because the baseline recorded it; the committed sibling transition is verified separately without rewriting that baseline. Task closure still makes no claim about product acceptance.
