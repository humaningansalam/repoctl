# Template / repoctl Separation Contract

Status: implementation target for the release after v0.15.3.
Source baseline: v0.15.3 at `3b0780fc1366e2171eca1075cb664be80c71b75f`.

## Responsibilities

**agent-handoff-template** owns the human-authored Task, Parent Task, Board registry,
and four-field Handoff document meanings, plus role guidance for PRD, spec, ADR,
research and workflow documents. Its public artifact is readable Markdown and
versioned conformance fixtures. It neither installs nor invokes repoctl.

**repoctl** owns validation and parsing of that document contract, repository and
Git identity, Task lifecycle and machine state, Chosen closure, Graph and Knowledge
queries, freshness/completeness/provenance, read-only diagnosis, upgrade and
machine-facing JSON. It never writes the meaning of Goal, Chosen, decisions,
Handoff or acceptance criteria.

**external agents/runtimes** own model selection, orchestration, code editing,
actual tests, commits, PRs, deployment and scheduling. Neither package stores an
agent hierarchy, execution queue or approval interpretation.

The only shared boundary is a versioned *public document contract*. Repoctl must
not import files by reaching into a sibling template repository. A contract
artifact or explicitly pinned compatible installed template supplies its Markdown
schemas and fixture inputs. No duplicate hand-maintained normative prose.

## Independent versions

- Template document contract: `1.0.0` initially; its own version is independent
  of the repoctl runtime/release number.
- Target repoctl release: a **new version after 0.15.3**, never altered 0.15.3
  bytes. The initial implementation supports `>=1.0.0,<2.0.0` of the template
  document contract only after cross-version tests pass.
- JSON machine contract and Task/Knowledge/Graph storage schema versions remain
  repoctl-owned. They must not be presented as the template document version.
- Patch/minor template changes preserve the four Handoff field names, core Task
  section meanings and Board registry parseability; incompatible changes require
  a major contract version and an explicit migration.

| Runtime | Template document contract | Upgrade disposition |
| --- | --- | --- |
| v0.15.3 combined source | pre-separation baseline | read as historical evidence; do not republish changed bytes |
| next repoctl runtime | 1.x | supported only when conformance suite passes |
| next repoctl runtime | 2.x or unknown | fail closed with a compatibility diagnostic |

A supported runtime MUST reject every explicit unsupported template major *before*
interpreting or mutating Task/Board documents: Task lifecycle and migrations,
Backlog commands, `check`, and other Task/Board validators. Rejection must
not create or append debug events or task-state receipts under
`docs/tasks/.repoctl-state/`; the Board, live/archived Task trees and task-state
bytes must remain identical. The unversioned compatibility lane is distinct
from an explicitly unsupported major.

## Conformance and release gate

Template publishes representative and negative Markdown fixtures for Task,
Parent Task, Board, Handoff and Context Docs. The fixtures include missing and
duplicate sections, generated Handoff, malformed paths, locale-neutral prose,
and incompatible major version. Repoctl consumes the **same fixture bytes**;
both release pipelines verify their digest/identity. Neither fixture content
nor a test filename is hardcoded as a general project rule.

Template-only smoke test: extract the template artifact into a clean directory,
read and author the documented Task/Handoff/Board forms without installing
repoctl, and check no executable runtime or provider files are included.

Repoctl-only smoke test: extract runtime files with a pinned template contract
fixture, invoke public help/version/read-only diagnostics, and verify explicit
unsupported-contract errors instead of inferring missing human meaning.

Integration test: pair released artifacts across supported and unsupported
contract versions, test parsing/validation and JSON projection, and confirm
that authored Goal, Chosen, Notes and Handoff are neither rewritten nor inferred.
Release gate also checks source file identity, archive SHA-256, license,
reproducibility and independently installable contents.

## Upgrade, extraction and rollback

The current combined manifest is a migration source, not evidence that
separation is complete. Until a destination is explicitly provided, produce
extraction-ready template-only and repoctl-only archives, a manifest mapping
source paths to their single owner, and the compatibility matrix; do not create
or push a remote repository.

A staged physical split must preserve Git history, release artifact identity,
adopter-owned documents and Task/Knowledge/receipt state. Upgrade should verify
the source and target contract versions, preserve the exact old artifact for
rollback, run a canary on a separate adopter workspace, and then widen rollout
only with an explicit owner decision. No automatic re-migration of 31 adopters.

## Invariants across both packages

- Source evidence first; confirmed relations distinct from lexical candidates.
- Unavailable, partial, ambiguous, stale and not-found remain distinguishable.
- Unknown relation or missing optional Graph/Knowledge enrichment never becomes
  zero-impact or a fabricated success.
- Direct file/rg reads remain valid when no Graph build is warranted.
- Read-only queries do not mutate Task, scope, Board or approval state.
- JSON action priority distinguishes required recovery, recommended enrichment
  and conditional pause/transfer steps; actual work continuation comes first
  when source evidence is already sufficient.
- No project-specific route, symbol, language, repository layout or scorer is
  elevated into core behavior.
- AI workslop warnings retain source examples and evidence and do not delete
  authored reasoning or convert test quantity into product-value proof.
