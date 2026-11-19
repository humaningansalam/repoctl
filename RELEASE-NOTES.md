# repoctl 1.0.0

Unreleased.

This release replaces the task-control protocol with a single Markdown Task model.

- Parentage is an optional relationship between Tasks. It requires no coordinator role, special headings, or separate parent template.
- Handoff file and command hints are optional. Reading and updating work requires no binding ceremony.
- Task completion records status and work notes. Git ownership acknowledgements, completion receipts, catalogue replay, and metadata gates are removed from the lifecycle.
- Board membership is optional. Area labels do not determine which work a Task may perform.
- Legacy embedded-runtime upgrades, state migrations, semantic workslop checks, and release field gates are removed from the runtime.
- Source navigation and saved decisions remain separate from Task status.
- Development and packaging use the Git source checkout and one archive builder. Generated manifests and checksums no longer freeze ordinary source changes to an old release.

The Task and CLI changes are intentionally breaking. Existing authored Markdown and historical data remain intact; retired machine state is not a prerequisite for continuing current work. Document contracts 1.x and 2.x share the readable Task fields supported by this runtime.
