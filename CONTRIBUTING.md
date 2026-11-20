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

repoctl owns Task parsing, record operations, source analysis and CLI behavior. The separate agent-handoff-template owns its starting Markdown documents. Update CLI help and the relevant interface documentation when behavior changes.
