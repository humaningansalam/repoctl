# repoctl

repoctl manages Markdown Tasks, searches project source and stores authored Knowledge notes. The separate [agent-handoff-template](https://github.com/humaningansalam/agent-handoff-template) provides optional starting documents.

## Run from Git

Python 3.11 or newer is required. Core commands use the standard library.

```sh
./scripts/repoctl --help
```

The launcher uses the checkout's `.venv` when present, otherwise `python3`. Set `PYTHON` to select an interpreter. Install development dependencies and optional structural analyzers with:

```sh
uv sync --locked --extra structural
```

Commands discover the workspace by walking upward from the current directory to the nearest `docs/tasks/`, `docs/repoctl.json` or Git boundary. Select a directory explicitly with `--workspace-root` or `REPOCTL_WORKSPACE_ROOT`. For example, from a workspace beside the repoctl checkout:

```sh
../repoctl/scripts/repoctl --workspace-root . task list --json
```

An adopter's `scripts/repoctl` wrapper can resolve its workspace from its own location and call this Git checkout through a relative path. Updating the checkout updates the runtime used by those wrappers.

## Task documents

Each Task is an editable Markdown file under `docs/tasks/`. Frontmatter contains its ID, title and status. The body holds the intended outcome, current findings, decisions, next action and eventual result.

```sh
./scripts/repoctl task create "Change title" --start --json
./scripts/repoctl task show T-... --json
./scripts/repoctl task finish T-... --json
```

Add `--body-file <PATH>` when creating a Task to use an existing UTF-8 Markdown body. Edit the resulting document directly as the work changes.

`task list` lists current records. `task resume` reads a selected Task's Handoff, or returns candidates when several Tasks are live. `task show --section <HEADING>` reads a named section.

`task start` sets the status to `doing`; `task block` sets it to `blocked`. Finishing or canceling records `done` or `canceled` and moves that Task to `docs/archive/tasks/`. An optional finish summary or block/cancel reason records the supplied text in the Task. Each command's `--help` lists its text and file inputs.

`parent` and `depends_on` link related Tasks. Each Task has its own status. Repository, area and owner metadata can describe the work. `docs/BOARD.md` is an optional overview; `backlog` commands edit its deferred-work entries.

## Repositories and source navigation

`repo list` shows registered repositories and detected candidates. `repo adopt` registers a selected Git root. Product repositories may live directly in `repos/` or in a collection under `repos/<name>/`.

Use `--repo-id <ID>` to select a repository for source operations:

- `index code` reads syntax and import facts from source files.
- `graph build` refreshes the derived source index and available structural relationships.
- `graph search` returns matching file snippets, with `--limit` controlling the result count.
- `graph query` follows indexed file, symbol and dependency relationships.
- `graph workflow` finds the implementation of a route or symbol.

Graph state lives under `.repoctl-state/`. Query results describe the sources and analysis coverage. See the [Graph contract](docs/contracts/repoctl-graph-contract.md) for selectors and result fields.

## Knowledge

Knowledge is an independently authored fact, decision or explanation kept for later use. `knowledge add` requires a claim through `--claim` or `--claim-file`. A reason, free-form `--kind` label, sources, applicability paths, replacements and author are optional.

Knowledge commands select workspace notes by default. `--repo-id <ID>` associates a note with that repository ID. `--source <PATH>` cites an existing workspace-relative file and records its content hash. `--applies-to <PATH>` records a path relative to the associated repository, or to the workspace for workspace notes.

`knowledge query` searches active notes and reports which terms matched. A negative `--limit` returns all matches; zero returns counts only. `knowledge show` reads one record. Source observations distinguish `unchanged`, `changed`, `missing` and `unavailable`; `unavailable` means the file could not be read. Use `--replaces <ID>` when a new note corrects an earlier one; `query --include-superseded` includes replaced notes.

## JSON and development

Use `--json` for structured command results. The [JSON interface](docs/contracts/repoctl-json-contract.md) describes the response envelope, Task results and Knowledge observations.

```sh
uv run --locked --extra structural pytest -q
git diff --check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidance.
