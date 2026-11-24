# Contributing to repoctl

Describe the problem, the behavior that changes and the checks you ran. Issues and pull requests may use English or Korean.

## Development

Work in the repoctl Git checkout:

```sh
uv sync --locked --extra structural
uv run --locked --extra structural pytest -q
git diff --check
```

During development, run the existing tests relevant to the change. A regression case should reproduce the failure and check its observable result. Report optional analyzer coverage alongside the test result.

## Code map

| Responsibility | Code |
| --- | --- |
| Commands and JSON results | `cli.py` |
| Task documents and edits | `tasks.py`, `markdown.py` |
| Board backlog entries | `board.py` |
| Repository selection and Git files | `repositories.py`, `git.py`, `inventory.py` |
| Source search and SQLite index | `source_search.py`, `source_index.py` |
| Source relationships and derived state | `graph.py`, `graph_store.py`, `graph_workflow.py` |
| Language analysis | `code_index.py`, `language_profiles.py`, `graph_*_provider.py`, `providers/` |
| Authored Knowledge notes | `knowledge_records.py` |
| Atomic files and writer locks | `io.py` |

These modules live under `tools/repoctl/`. Task edits belong to the Task module; the CLI handles arguments and presents results. Source indexes are derived data. Knowledge notes remain independently authored.

The separate [agent-handoff-template](https://github.com/humaningansalam/agent-handoff-template) owns its starting documents. Update CLI help and the relevant [interface documentation](docs/contracts/repoctl-json-contract.md) when behavior changes.

## Pull requests

Keep a PR focused on one logical change, including the code, tests and documentation needed to explain it. Describe the resulting behavior and actual validation. The `verify` check runs the test suite and whitespace check. Squash-merge the reviewed PR after that check passes, using a title that describes the change, then delete its branch.
