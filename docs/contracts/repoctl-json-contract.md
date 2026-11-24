# JSON interface

Pass `--json` to receive a command result as one object:

```json
{
  "ok": true,
  "command": "task.list",
  "data": {"tasks": []},
  "warnings": [],
  "problems": []
}
```

`command` identifies the operation. `data` contains its result. Each diagnostic has a `code` and `message`, with `severity` and a source `path` where relevant. Warnings provide context; problems describe errors and set `ok` to `false`.

Successful commands exit with status 0. A result containing problems exits with status 1. Rejected arguments and command failures exit with status 2.

## Task results

Task identity includes `id`, `title`, `status`, `path`, `repo_id` and `parent`. Task paths are relative to the workspace.

| Command | Result |
| --- | --- |
| `task list` | `data.tasks`; excludes `done` and `canceled` by default |
| `task list --all` | All Tasks, with optional repository filter |
| `task list --status <STATUS>` | Tasks with the selected status, with optional repository filter |
| `task show` | Task identity, parsed `frontmatter` and authored `body` |
| `task show --summary` | Task identity |
| `task show --section <HEADING>` | `task`, `section` and that section's `body` |
| `task resume <ID>` | `task` and authored `handoff`; `--full` includes body and frontmatter |
| `task resume` | The single live Task, or `task: null` and a `candidates` list |
| `task create`, `start`, `block`, `finish`, `cancel` | `task_id`, resulting `path` and `status` |

Task statuses are `todo`, `doing`, `blocked`, `done` and `canceled`. Finishing and canceling update the selected document in place. Task selection reads `docs/tasks/` and `docs/archive/tasks/`; location does not determine status. Parent and dependency fields describe relationships; each Task changes status independently.

`task create --body-file` reads an authored UTF-8 Markdown body. Lifecycle commands update the Task metadata while retaining its path and body. Explicit finish summaries and block/cancel reasons are appended as authored notes.

## Knowledge results

Knowledge records contain an ID, repository association and claim. Optional fields hold a kind, reason, source references, applicability paths, replaced record IDs and author.

Workspace notes use an empty `repo_id`. `--repo-id <ID>` selects records associated with that ID. A cited source is an existing workspace-relative file; its saved reference contains a `content_sha256`.

`knowledge add` returns `record` and `record_path`. Query entries include `exact_match` and `matched_terms` for the match. Queries select active records by default; `--include-superseded` also returns replaced records. A negative `--limit` returns all matches; zero returns counts only.

Record `status` is `active` or `superseded`. `source_statuses` independently observes each cited file as `unchanged`, `changed`, `missing` or `unavailable`. An unavailable source could not be read. The saved source hash identifies the cited content. Applicability in `applies_to.paths` is relative to the associated repository, or to the workspace for workspace records.

## Source navigation

Source-operation paths are relative to the selected repository, or to the workspace when `--workspace` is selected. `graph search` returns matching source snippets. Typed Graph queries include relationship resolution and analysis coverage.

The [Graph contract](repoctl-graph-contract.md) defines the source-search and relationship results. Use each command's `--help` for its selectors and optional fields.
