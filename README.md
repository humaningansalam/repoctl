# repoctl

repoctl manages Markdown Tasks and helps navigate project source and saved decisions. The separate [agent-handoff-template](https://github.com/humaningansalam/agent-handoff-template) provides optional starting documents.

## Run from Git

Python 3.11 or newer is required. Core commands use the standard library.

```sh
./scripts/repoctl --help
```

For development and optional structural analysis:

```sh
uv sync --locked --extra structural
```

Select the workspace explicitly. For example, from a workspace beside the repoctl checkout:

```sh
REPOCTL_WORKSPACE_ROOT="$PWD" ../repoctl/scripts/repoctl task list --json
```

An adopter's small `scripts/repoctl` wrapper can locate its workspace from the wrapper's own directory and call the Git checkout through a relative path. `REPOCTL_HOME` may override that runtime location. Updating the checkout updates the runtime used by these wrappers.

The launcher uses its own `.venv` when present, otherwise `python3`. An explicit `PYTHON` override selects the interpreter. Commands do not install dependencies.

## Tasks

Each Task is a Markdown file with an ID, title, status, and the information useful to continue the work. `parent` optionally links another Task. A Task can have children and do work itself; repository selection and area labels are independent of that relationship.

Keep the goal, next action, and completion condition clear. A Handoff may include a file or command hint when useful. Missing hints or a missing Handoff do not prevent creating, starting, or finishing a Task. Duplicate sections are diagnosed instead of silently choosing one.

Use `task create`, `start`, `show`, `resume`, `finish`, `block`, or `cancel` to manage the record. `task doctor` previews finish readiness without changing it. `task log append` and `task discovery add` record work notes and current file scope. Pass `--summary` or `--summary-file` to `task finish` to record the outcome. Use `task --help` for arguments.

Task files are the current record. A Board is an optional overview. Status changes manage that record; actual product checks and delivery remain part of the project's development workflow. Existing project files and historical records are preserved.

## Source and decisions

`graph build` creates a disposable source index. `graph search` locates relevant sources; `graph query` and `graph workflow` follow recorded relationships. Results identify sources and distinguish resolved relationships, candidates, and incomplete analysis.

`knowledge add` saves reusable decisions with their reasons and source references. `knowledge query` and `show` retrieve them. Read the linked current source when applying a saved decision.

Tasks, source files, and Knowledge records remain readable without derived Graph state. See the [Graph contract](docs/contracts/repoctl-graph-contract.md) and [JSON contract](docs/contracts/repoctl-json-contract.md).

## Development

```sh
uv run --locked --extra structural pytest -q
python3 .github/scripts/build_release_archive.py
```

The release builder packages the current source and generates its manifest and checksums in `dist/`. Tests use the same builder. Local adopters can keep running directly from Git.
