from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from .io import Problem, RepoctlError, atomic_write, find_workspace_root, repoctl_lock


class RepoctlArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        error = RepoctlError(message, code="argparse_error")
        error.command = self.prog.replace("repoctl", "", 1).strip().replace(" ", ".")
        raise error


def _problem(value: Any) -> dict[str, Any]:
    return value.to_dict() if hasattr(value, "to_dict") else dict(value)


def _emit(args: argparse.Namespace, data: Any, problems=(), warnings=(), *, text: str = "") -> int:
    items = [_problem(p) for p in problems]
    errors = [p for p in items if p.get("severity", "error") == "error"]
    warnings = [*[_problem(p) for p in warnings], *[p for p in items if p.get("severity") == "warning"]]
    payload = {"ok": not errors, "command": args.command, "data": data, "problems": errors, "warnings": warnings}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        if text:
            print(text.rstrip())
        elif data:
            print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        for p in [*errors, *warnings]:
            print(f"{p.get('code', 'error')}: {p['message']}", file=sys.stderr)
    return 1 if errors else 0


def _root(args: argparse.Namespace) -> Path:
    return find_workspace_root(getattr(args, "workspace_root", None))


def _target(root: Path, args: argparse.Namespace):
    from .repositories import source_target
    return source_target(root, repo_id=getattr(args, "repo_id", None), workspace=getattr(args, "workspace", False))


def _version_data() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    return {"version": version, "runtime_root": str(root)}


def cmd_version(args: argparse.Namespace) -> int:
    data = _version_data()
    return _emit(args, data, text=data["version"])


def cmd_check(args: argparse.Namespace) -> int:
    from .repositories import repo_check_problems, repo_layout
    from .tasks import load_tasks, validate_tasks
    root = _root(args)
    tasks = load_tasks(root)
    problems = [*validate_tasks(tasks), *repo_check_problems(root, repo_layout(root))]
    return _emit(args, {"task_count": len(tasks)}, problems,
                 text=f"Checked {len(tasks)} Task documents.")


def cmd_repo(args: argparse.Namespace) -> int:
    from .repositories import adopt_repositories, repo_check_problems, repo_layout, require_repo_target
    root = _root(args)
    action = args.command.split(".")[-1]
    if action == "show":
        target = require_repo_target(root, args.repo_id)
        return _emit(args, {"repository": target.to_dict()}, text=f"{target.id} {target.display_path}")
    if action == "adopt":
        with repoctl_lock(root):
            layout = adopt_repositories(root, all_candidates=args.all, path=args.path or "", repo_id=args.repo_id or "")
    else:
        layout = repo_layout(root)
    problems = repo_check_problems(root, layout) if action == "check" else []
    return _emit(args, layout.to_dict(), problems)


def _task_data(task: Any, *, full: bool = False) -> dict[str, Any]:
    data = {"id": task.id, "title": task.title, "status": task.status,
            "path": task.rel_path, "repo_id": task.repo_id, "parent": task.parent}
    if full:
        data.update(frontmatter=task.frontmatter, body=task.body)
    return data


def cmd_task_list(args: argparse.Namespace) -> int:
    from .tasks import CLOSED_STATUSES, load_tasks
    tasks = load_tasks(_root(args))
    if args.status is not None:
        tasks = [task for task in tasks if task.status == args.status]
    elif not args.all:
        tasks = [task for task in tasks if task.status not in CLOSED_STATUSES]
    if args.repo_id is not None:
        tasks = [task for task in tasks if task.repo_id == args.repo_id]
    return _emit(args, {"tasks": [_task_data(task) for task in tasks]},
                 text="\n".join(f"{task.id} {task.status} {task.title}" for task in tasks) or "No Tasks.")


def cmd_task_show(args: argparse.Namespace) -> int:
    from .markdown import find_section
    from .tasks import resolve_task
    task = resolve_task(_root(args), args.task_id)
    data = _task_data(task, full=not args.summary)
    text = f"{task.id} {task.status} {task.title}" if args.summary else task.body
    if args.section:
        section = find_section(task.body, args.section)
        text = task.body[section.body_start:section.end].strip()
        data = {"task": _task_data(task), "section": args.section, "body": text}
    return _emit(args, data, text=text)


def cmd_task_resume(args: argparse.Namespace) -> int:
    from .tasks import OPEN_STATUSES, load_tasks, resolve_task, task_handoff_body
    root = _root(args)
    if args.task_id:
        task = resolve_task(root, args.task_id)
    else:
        candidates = [task for task in load_tasks(root) if task.status in OPEN_STATUSES]
        if len(candidates) != 1:
            return _emit(args, {"task": None, "candidates": [_task_data(task) for task in candidates]},
                         text="Select a Task by ID." if candidates else "No live Tasks.")
        task = candidates[0]
    data = {"task": _task_data(task, full=args.full), "handoff": task_handoff_body(task)}
    return _emit(args, data, text=f"{task.id} {task.title}\n\n{data['handoff'] or task.body}")


def cmd_task_create(args: argparse.Namespace) -> int:
    from .tasks import apply_task_edit, prepare_task_create
    root = _root(args)
    body = None
    if args.body_file:
        path = Path(args.body_file)
        with (path if path.is_absolute() else root / path).open(encoding="utf-8", newline="") as handle:
            body = handle.read()
    with repoctl_lock(root):
        edit = prepare_task_create(title=args.title, slug=args.slug, area=args.area, owner=args.owner,
                                   parent=args.parent, repo_ref=args.repo_ref, repo_id=args.repo_id,
                                   depends_on=args.depends_on, body=body, start=args.start)
        apply_task_edit(root, edit)
    return _emit(args, {"task_id": edit.task_id, "path": edit.path,
                        "status": "doing" if args.start else "todo"}, text=f"Created {edit.task_id}: {edit.path}")


def _read_text_arg(root: Path, value: str | None, file: str | None) -> str:
    if file:
        path = Path(file)
        return (path if path.is_absolute() else root / path).read_text(encoding="utf-8").strip()
    return (value or "").strip()


def cmd_task_transition(args: argparse.Namespace) -> int:
    from .tasks import apply_task_edit, prepare_task_block, prepare_task_cancel, prepare_task_finish, prepare_task_start
    root = _root(args)
    operation = args.command.split(".")[-1]
    with repoctl_lock(root):
        if operation == "finish":
            edit = prepare_task_finish(root, args.task_id, summary=_read_text_arg(root, args.summary, args.summary_file))
        elif operation == "start":
            edit = prepare_task_start(root, args.task_id)
        else:
            reason = _read_text_arg(root, args.reason, args.reason_file)
            prepare = prepare_task_cancel if operation == "cancel" else prepare_task_block
            edit = prepare(root, args.task_id, reason=reason)
        apply_task_edit(root, edit)
    status = {"start": "doing", "finish": "done", "cancel": "canceled", "block": "blocked"}[operation]
    return _emit(args, {"task_id": edit.task_id, "path": edit.path, "status": status},
                 text=f"{edit.task_id}: {status}")


def _board_text(root: Path) -> str:
    path = root / "docs/BOARD.md"
    from .markdown import has_section
    text = path.read_text(encoding="utf-8") if path.exists() else "# Board\n"
    if not has_section(text, "Backlog"):
        text = text.rstrip() + "\n\n## Backlog\n"
    return text


def cmd_backlog(args: argparse.Namespace) -> int:
    from .board import append_backlog_item, read_backlog_items, remove_backlog_item, resolve_backlog_item
    root = _root(args)
    operation = args.command.split(".")[-1]
    if operation in {"add", "remove"}:
        with repoctl_lock(root):
            source = _board_text(root)
            if operation == "add":
                body = _read_text_arg(root, "", args.body_file)
                updated = append_backlog_item(source, args.title, body)
                item = read_backlog_items(updated)[-1]
            else:
                updated, item = remove_backlog_item(source, args.backlog_id)
            atomic_write(root / "docs/BOARD.md", updated)
        return _emit(args, {"item": item.to_dict()}, text=f"{operation}: {item.id}")
    source = _board_text(root)
    if operation == "show":
        item = resolve_backlog_item(source, args.backlog_id)
        return _emit(args, {"item": item.to_dict()}, text=item.raw)
    items = read_backlog_items(source)
    return _emit(args, {"items": [item.to_dict() for item in items]},
                 text="\n\n".join(item.raw for item in items) or "Backlog is empty.")


def cmd_index_code(args: argparse.Namespace) -> int:
    from .code_index import build_code_index
    root = _root(args)
    entries, problems, data = build_code_index(root, changed=args.changed, limit=args.limit, target=_target(root, args))
    return _emit(args, {**data, "files": [entry.to_dict() for entry in entries]}, problems)


def _snapshot_summary(snapshot: Any) -> dict[str, Any]:
    return {"repository": snapshot.repository, "snapshot_digest": snapshot.snapshot_digest,
            "source_count": len(snapshot.sources), "node_count": len(snapshot.nodes),
            "edge_count": len(snapshot.edges), "completeness": snapshot.completeness}


def cmd_graph_build(args: argparse.Namespace) -> int:
    from .graph_store import materialize_graph
    root = _root(args)
    target = _target(root, args)
    with repoctl_lock(root, resource="graph-" + target.id):
        snapshot, problems, meta = materialize_graph(root, target=target, rebuild=args.rebuild)
    data = {"repository": target.to_dict(), "summary": _snapshot_summary(snapshot) if snapshot else None,
            "materialization": meta.get("materialization", {})}
    if args.full:
        data["snapshot"] = snapshot.to_dict() if snapshot else None
    return _emit(args, data, problems)


def cmd_graph_query(args: argparse.Namespace) -> int:
    from .graph import query_graph
    from .graph_store import graph_materialization_freshness, load_materialized_graph
    root = _root(args)
    target = _target(root, args)
    snapshot, problems, meta = load_materialized_graph(root, target=target)
    if snapshot is None or any(p.severity == "error" for p in problems):
        return _emit(args, {"result": None, **meta}, problems)
    freshness, freshness_problems = graph_materialization_freshness(root, target=target, snapshot=snapshot)
    problems.extend(freshness_problems)
    if any(p.severity == "error" for p in problems):
        return _emit(args, {"result": None, "freshness": freshness}, problems)
    keys = ("file", "import_ref", "symbol", "callers_of", "callees_of", "impact_file",
            "impact_symbol", "in_file", "depth")
    result, query_problems = query_graph(snapshot,
                                        **{key: getattr(args, key) for key in keys})
    problems.extend(query_problems)
    warnings = list((result or {}).get("warnings", []))
    if freshness.get("status") == "stale":
        warnings.append({"code": "graph_snapshot_stale", "message": "Sources changed since Graph was built; rebuild to refresh relations."})
    data = {"result": result,
            "query_status": (result or {}).get("query_status", "unavailable"),
            "repository": target.to_dict(), "snapshot_digest": snapshot.snapshot_digest,
            "completeness": (result or {}).get("completeness", snapshot.completeness),
            "freshness": freshness,
            "materialization": meta.get("materialization", {})}
    return _emit(args, data, problems, warnings)


def cmd_graph_workflow(args: argparse.Namespace) -> int:
    from .graph_store import graph_materialization_freshness, load_materialized_graph
    from .graph_workflow import query_graph_workflow
    root = _root(args)
    target = _target(root, args)
    snapshot, problems, meta = load_materialized_graph(root, target=target)
    if snapshot is None:
        return _emit(args, {"result": None, **meta}, problems)
    freshness, freshness_problems = graph_materialization_freshness(root, target=target, snapshot=snapshot)
    problems.extend(freshness_problems)
    if any(p.severity == "error" for p in problems):
        return _emit(args, {"result": None, "freshness": freshness}, problems)
    result = query_graph_workflow(snapshot, target=target, route=args.route or "",
                                  symbol=args.symbol or "", in_file=args.in_file, freshness=freshness)
    if result["status"] in {"invalid", "ambiguous"}:
        problems.append(Problem("error", "graph_workflow_" + result["status"],
                                result.get("problem", "Multiple matches; select a file or qualified symbol.")))
    data = {"result": result, "query_status": result["status"], "repository": target.to_dict(),
             "completeness": snapshot.completeness,
            "materialization": meta.get("materialization", {}),
            "freshness": freshness}
    warnings = [{"code": "graph_workflow_partial", "message": message} for message in result.get("warnings", [])]
    return _emit(args, data, problems, warnings)


def cmd_graph_search(args: argparse.Namespace) -> int:
    from .source_search import search_sources, render_source_search_markdown, render_source_search_text
    root = _root(args)
    data, problems = search_sources(root, target=_target(root, args), query=args.query, limit=args.limit)
    render = render_source_search_markdown if args.format == "markdown" else render_source_search_text
    return _emit(args, data, problems, text="" if args.json or not data else render(data))


def cmd_knowledge(args: argparse.Namespace) -> int:
    from .knowledge_records import add_knowledge_record, check_knowledge_records, knowledge_status, query_knowledge_records, show_knowledge_record
    root = _root(args)
    repo_id = args.repo_id or ""
    operation = args.command.split(".")[-1]
    warnings: list[Any] = []
    if operation == "add":
        claim = _read_text_arg(root, args.claim, args.claim_file)
        reason = _read_text_arg(root, args.reason, args.reason_file)
        with repoctl_lock(root):
            data, problems, warnings = add_knowledge_record(
                root, repo_id=repo_id, kind=args.kind, claim=claim, reason=reason,
                sources=args.source, applies_to=args.applies_to, replaces=args.replaces, author=args.author)
    elif operation == "show":
        data, problems = show_knowledge_record(root, record_id=args.record_id, repo_id=repo_id)
    elif operation == "check":
        data, problems = check_knowledge_records(root, repo_id=repo_id)
    elif operation == "query":
        data, problems, warnings = query_knowledge_records(root, repo_id=repo_id, query=args.query,
                include_superseded=args.include_superseded, limit=args.limit)
    else:
        data = knowledge_status(root, repo_id=repo_id)
        problems = data.pop("problems", [])
    return _emit(args, data, problems, warnings)


def build_parser() -> argparse.ArgumentParser:
    parser = RepoctlArgumentParser(prog="repoctl", description="Task documents, repository observations and source search.")
    parser.add_argument("--version", action="store_true", help="show runtime version")
    parser.add_argument("--json", action="store_true", help="return a JSON result")
    parser.add_argument("--workspace-root", help="workspace directory; defaults to REPOCTL_WORKSPACE_ROOT or cwd")
    parser.set_defaults(command="")
    groups = parser.add_subparsers(dest="group")

    def command(sub, name: str, identity: str, func, help: str):
        child = sub.add_parser(name, help=help, description=help)
        child.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="return a JSON result")
        child.add_argument("--workspace-root", default=argparse.SUPPRESS, help="workspace directory")
        child.set_defaults(command=identity, func=func)
        return child

    def group(name: str, help: str):
        child = groups.add_parser(name, help=help, description=help)
        return child.add_subparsers(dest=f"{name}_command", required=True)

    def scope(child, *, workspace: bool = True):
        selected = child.add_mutually_exclusive_group()
        selected.add_argument("--repo-id", help="repository ID")
        if workspace:
            selected.add_argument("--workspace", action="store_true", help="select workspace documents")

    def full(child):
        child.add_argument("--full", action="store_true", help="include full source data and diagnostics")

    def text_input(child, name: str, *, required: bool = False):
        pair = child.add_mutually_exclusive_group(required=required)
        pair.add_argument(f"--{name}", default="", help=f"{name.replace('-', ' ')} text")
        pair.add_argument(f"--{name}-file", default="", help=f"UTF-8 file containing {name.replace('-', ' ')}")

    command(groups, "version", "version", cmd_version, "Show runtime version.")
    command(groups, "check", "check", cmd_check, "Check all Task documents and repository registration.")

    repo = group("repo", "Select and register repositories.")
    command(repo, "list", "repo.list", cmd_repo, "List repositories and unregistered candidates.")
    show = command(repo, "show", "repo.show", cmd_repo, "Show a repository.")
    show.add_argument("repo_id", help="registered repository ID")
    command(repo, "check", "repo.check", cmd_repo, "Check repository registration.")
    adopt = command(repo, "adopt", "repo.adopt", cmd_repo, "Register a repository without generating indexes.")
    adopt.add_argument("path", nargs="?", help="workspace-relative repository path")
    adopt.add_argument("--all", action="store_true", help="register all detected candidates")
    adopt.add_argument("--id", dest="repo_id", help="repository ID")

    task = group("task", "Create and update independent Task documents.")
    create = command(task, "create", "task.create", cmd_task_create, "Create one Task.")
    create.add_argument("title", help="Task title")
    create.add_argument("--slug", help="optional filename stem")
    create.add_argument("--body-file", help="UTF-8 Markdown body, preserved as supplied")
    create.add_argument("--area", default="", help="free-form area label")
    create.add_argument("--owner", default="", help="Task owner")
    create.add_argument("--parent", default="", help="parent Task ID; lifecycle remains independent")
    create.add_argument("--repo-id", default="", help="registered repository ID")
    create.add_argument("--repo-ref", default="", help="optional descriptive repository reference")
    create.add_argument("--depends-on", action="append", default=[], help="advisory Task dependency; repeat as needed")
    create.add_argument("--start", action="store_true", help="create in doing status")
    listing = command(task, "list", "task.list", cmd_task_list, "List open Task documents.")
    selection = listing.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="include completed and canceled Tasks")
    selection.add_argument("--status", help="select one status, regardless of document location")
    listing.add_argument("--repo-id", help="filter by repository ID")
    show = command(task, "show", "task.show", cmd_task_show, "Read one Task.")
    show.add_argument("task_id", help="Task ID or document path")
    show.add_argument("--summary", action="store_true", help="omit body and frontmatter")
    show.add_argument("--section", help="read one named Markdown section")
    resume = command(task, "resume", "task.resume", cmd_task_resume, "Read authored Handoff or list available Tasks.")
    resume.add_argument("task_id", nargs="?", help="Task ID or document path")
    full(resume)
    for name, help in (
        ("start", "Set a Task to doing."),
        ("finish", "Mark a Task done without moving its document."),
        ("cancel", "Mark a Task canceled without moving its document."),
        ("block", "Set a Task to blocked."),
    ):
        child = command(task, name, f"task.{name}", cmd_task_transition, help)
        child.add_argument("task_id", help="Task ID or document path")
        if name in {"cancel", "block"}:
            text_input(child, "reason")
        if name == "finish":
            text_input(child, "summary")
    backlog = group("backlog", "Edit optional Board backlog entries.")
    command(backlog, "list", "backlog.list", cmd_backlog, "List backlog entries.")
    add = command(backlog, "add", "backlog.add", cmd_backlog, "Add a backlog entry.")
    add.add_argument("title", help="entry title")
    add.add_argument("--body-file", help="UTF-8 file containing the entry")
    for name in ("show", "remove"):
        child = command(backlog, name, f"backlog.{name}", cmd_backlog, f"{name.title()} a backlog entry.")
        child.add_argument("backlog_id", help="backlog entry ID")

    index = group("index", "Inspect source files.")
    code = command(index, "code", "index.code", cmd_index_code, "Read syntax and import facts.")
    scope(code, workspace=False)
    code.add_argument("--changed", action="store_true", help="inspect Git changes")
    code.add_argument("--limit", type=int, default=200, help="maximum files; -1 means all")

    graph = group("graph", "Build and query a derived source index.")
    build = command(graph, "build", "graph.build", cmd_graph_build, "Build or refresh Graph from source files.")
    scope(build, workspace=False)
    full(build)
    build.add_argument("--rebuild", action="store_true", help="rebuild provider caches")
    query = command(graph, "query", "graph.query", cmd_graph_query, "Query indexed files, symbols and relationships.")
    scope(query, workspace=False)
    for name in ("file", "symbol", "callers-of", "callees-of", "impact-file", "impact-symbol", "in-file"):
        query.add_argument(f"--{name}", default="", help=f"{name.replace('-', ' ')} selector")
    query.add_argument("--import", dest="import_ref", default="", help="import selector")
    query.add_argument("--depth", type=int, default=1, help="relationship traversal depth")
    workflow = command(graph, "workflow", "graph.workflow", cmd_graph_workflow, "Locate route or symbol implementation and relevant tests.")
    scope(workflow, workspace=False)
    selector = workflow.add_mutually_exclusive_group(required=True)
    selector.add_argument("--route", help="HTTP route literal")
    selector.add_argument("--symbol", help="symbol name")
    workflow.add_argument("--in-file", default="", help="repository-relative entrypoint file")
    search = command(graph, "search", "graph.search", cmd_graph_search, "Search source-linked code and documents.")
    scope(search)
    search.add_argument("query", help="search text")
    search.add_argument("--limit", type=int, default=20, help="maximum results; -1 means all")
    search.add_argument("--format", choices=("text", "markdown"), default="text", help="human-readable format")

    knowledge = group("knowledge", "Store and query reusable claims with source citations.")
    for name in ("add", "show", "query", "check", "status"):
        child = command(knowledge, name, f"knowledge.{name}", cmd_knowledge, f"{name.title()} Knowledge records.")
        scope(child)
        if name == "add":
            child.add_argument("--kind", default="", help="optional free-form record kind")
            text_input(child, "claim", required=True)
            text_input(child, "reason")
            child.add_argument("--source", action="append", default=[], help="workspace-relative source; repeat as needed")
            child.add_argument("--applies-to", action="append", default=[], help="repository-relative file; repeat as needed")
            child.add_argument("--replaces", action="append", default=[], help="replaced record ID; repeat as needed")
            child.add_argument("--author", default="", help="record author")
        elif name == "show":
            child.add_argument("record_id", help="Knowledge record ID")
        elif name == "query":
            child.add_argument("query", help="search text")
            child.add_argument("--include-superseded", action="store_true", help="include replaced records")
            child.add_argument("--limit", type=int, default=10, help="maximum returned records; -1 means all, 0 returns counts only")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = argparse.Namespace(command="", json="--json" in raw)
    try:
        args = parser.parse_args(raw)
        if args.version:
            args.command = "version"
            return cmd_version(args)
        if not hasattr(args, "func"):
            parser.print_help()
            return 0
        return args.func(args)
    except (RepoctlError, OSError, UnicodeError, ValueError) as error:
        code = getattr(error, "code", "io_error" if isinstance(error, OSError) else "invalid_input")
        args.command = getattr(error, "command", "") or args.command
        problem = {"severity": "error", "code": code, "message": str(error)}
        if getattr(error, "path", None):
            problem["path"] = error.path
        _emit(args, {}, [problem])
        return 2
