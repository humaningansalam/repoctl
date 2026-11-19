from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
import sys
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any

from .io import Problem, RepoctlError, atomic_write, find_workspace_root, repoctl_lock, write_temporary_text


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
    value = getattr(args, "workspace_root", None)
    if value:
        root = Path(value).expanduser().resolve()
        if not (root / "docs/tasks").is_dir():
            raise RepoctlError("workspace root has no docs/tasks directory", code="workspace_root_invalid", path=str(root))
        return root
    return find_workspace_root()


def _target(root: Path, args: argparse.Namespace):
    from .repositories import evidence_target
    return evidence_target(root, repo_id=getattr(args, "repo_id", None), workspace=getattr(args, "workspace", False))


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
    from .template_contract import require_compatible_template_contract
    root = _root(args)
    contract = require_compatible_template_contract(root)
    all_tasks = load_tasks(root, include_archived=True)
    tasks = all_tasks if args.include_archived else [task for task in all_tasks if not task.archived]
    paths = {task.rel_path for task in tasks}
    task_problems = [problem for problem in validate_tasks(all_tasks)
                     if args.include_archived or problem.path in paths or problem.code == "duplicate_task_id"]
    problems = [*task_problems, *repo_check_problems(root, repo_layout(root))]
    return _emit(args, {"task_count": len(tasks), "template_contract": contract}, problems,
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
    problems = repo_check_problems(root, layout) if action == "check" else list(layout.problems)
    return _emit(args, layout.to_dict(), problems)


def _task_data(task: Any, *, full: bool = False) -> dict[str, Any]:
    data = {"id": task.id, "title": task.title, "status": task.status,
            "path": task.rel_path, "repo_id": task.repo_id, "parent": task.parent, "archived": task.archived}
    if full:
        data.update(frontmatter=task.frontmatter, body=task.body)
    return data


def _write_changes(root: Path, writes: dict[Path, str], deletes=(), *, require_new=()) -> None:
    """Stage text first; restore the original files if a publish operation fails."""
    all_paths = set(writes) | set(deletes)
    root = root.resolve()
    for path in all_paths:
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise RepoctlError("write path is outside the workspace or is a symlink", code="unsafe_write_path", path=str(path))
        current = path.parent
        while current != root and current != current.parent:
            if current.is_symlink():
                raise RepoctlError("write path crosses a symlink", code="unsafe_write_path", path=str(path))
            current = current.parent
    for path in require_new:
        if path.exists():
            raise RepoctlError("destination already exists", code="destination_exists", path=str(path))
    originals = {p: p.read_bytes() if p.exists() else None for p in all_paths}
    staged: dict[Path, Path] = {}
    changed: list[Path] = []
    try:
        for path, text in writes.items():
            staged[path] = write_temporary_text(path, text)
        for path, temporary in staged.items():
            os.replace(temporary, path)
            changed.append(path)
        for path in deletes:
            path.unlink()
            changed.append(path)
    except Exception:
        for path in reversed(changed):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, original.decode("utf-8"))
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def apply_task_result(root: Path, result: dict[str, Any], *, extra_writes: dict[Path, str] | None = None) -> None:
    old = root / result["old_path"] if result.get("old_path") else None
    new = root / result["new_path"]
    expected = result.get("original_text")
    if old and expected is not None and old.read_bytes() != expected.encode("utf-8"):
        raise RepoctlError("Task changed after it was read; retry with the current document", code="task_changed", path=str(old))
    writes = {new: result["text"], **(extra_writes or {})}
    new_paths = [new] if old != new else []
    new_paths.extend(path for path in (extra_writes or {}) if not path.exists())
    _write_changes(root, writes, (old,) if old and old != new else (), require_new=new_paths)


def cmd_task_list(args: argparse.Namespace) -> int:
    from .tasks import load_tasks
    tasks = load_tasks(_root(args), include_archived=args.include_archived)
    if args.status:
        tasks = [t for t in tasks if t.status == args.status]
    if args.repo_id is not None:
        tasks = [t for t in tasks if t.repo_id == args.repo_id]
    return _emit(args, {"tasks": [_task_data(t) for t in tasks]},
                 text="\n".join(f"{t.id} {t.status} {t.title}" for t in tasks) or "No Tasks.")


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
    from .tasks import LIVE, load_tasks, resolve_task, task_handoff_body
    root = _root(args)
    if args.task_id:
        task = resolve_task(root, args.task_id)
    else:
        candidates = [t for t in load_tasks(root) if t.status in LIVE]
        if len(candidates) != 1:
            return _emit(args, {"task": None, "candidates": [_task_data(t) for t in candidates]},
                         text="Select a Task by ID." if candidates else "No live Tasks.")
        task = candidates[0]
    data = {"task": _task_data(task, full=args.full), "handoff": task_handoff_body(task)}
    return _emit(args, data, text=f"{task.id} {task.title}\n\n{data['handoff'] or task.body}")


def cmd_task_create(args: argparse.Namespace) -> int:
    from .markdown import replace_frontmatter_line
    from .tasks import create_task_file, utc_stamp
    root = _root(args)
    with repoctl_lock(root):
        result = create_task_file(root, title=args.title, slug=args.slug, area=args.area, owner=args.owner,
                                  parent=args.parent, repo_ref=args.repo_ref, repo_id=args.repo_id,
                                  depends_on=args.depends_on)
        if args.start:
            result["text"] = replace_frontmatter_line(result["text"], "status", "doing")
            result["text"] = replace_frontmatter_line(result["text"], "started_at", json.dumps(utc_stamp()))
        apply_task_result(root, result)
    return _emit(args, {"task_id": result["task_id"], "path": result["new_path"],
                        "status": "doing" if args.start else "todo"}, text=f"Created {result['task_id']}: {result['new_path']}")


def _read_text_arg(root: Path, value: str | None, file: str | None) -> str:
    if file:
        path = Path(file)
        return (path if path.is_absolute() else root / path).read_text(encoding="utf-8").strip()
    return (value or "").strip()


def cmd_task_transition(args: argparse.Namespace) -> int:
    from .tasks import block_task, cancel_task, finish_task, start_task
    root = _root(args)
    operation = args.command.split(".")[-1]
    extra: dict[Path, str] = {}
    with (nullcontext() if operation == "doctor" else repoctl_lock(root)):
        if operation in {"finish", "doctor"}:
            result = finish_task(root, args.task_id, summary=_read_text_arg(root, getattr(args, "summary", ""), getattr(args, "summary_file", "")))
        elif operation == "start":
            result = start_task(root, args.task_id)
        else:
            reason = _read_text_arg(root, args.reason, args.reason_file)
            result = (cancel_task if operation == "cancel" else block_task)(root, args.task_id, reason=reason)
        knowledge_path = ""
        if operation == "finish" and any(getattr(args, key, None) for key in (
            "knowledge_kind", "knowledge_claim", "knowledge_claim_file", "knowledge_reason", "knowledge_reason_file",
            "knowledge_applies_to", "knowledge_replaces", "knowledge_author",
        )):
            from .knowledge_records import prepare_knowledge_record
            task = result["task"]
            prepared, problems = prepare_knowledge_record(
                root, repo_id=task.repo_id, kind=args.knowledge_kind or "decision",
                claim=_read_text_arg(root, args.knowledge_claim, args.knowledge_claim_file),
                reason=_read_text_arg(root, args.knowledge_reason, args.knowledge_reason_file),
                sources=[result["new_path"]], source_bytes={result["new_path"]: result["text"].encode("utf-8")},
                applies_to=args.knowledge_applies_to, replaces=args.knowledge_replaces, author=args.knowledge_author,
            )
            if prepared is None or any(p.severity == "error" for p in problems):
                return _emit(args, {"task_id": args.task_id, "changed": False}, problems)
            extra[prepared.path] = prepared.text
            knowledge_path = str(prepared.path.relative_to(root))
        if operation != "doctor":
            apply_task_result(root, result, extra_writes=extra)
    data = {"task_id": result["task_id"], "path": result["new_path"], "changed": operation != "doctor"}
    if operation == "doctor":
        data["finish_ready"] = True
    if knowledge_path:
        data["knowledge_path"] = knowledge_path
    return _emit(args, data, text=f"{result['task_id']}: {'ready to finish' if operation == 'doctor' else operation}")


def cmd_task_log(args: argparse.Namespace) -> int:
    from .tasks import append_task_log
    root = _root(args)
    with repoctl_lock(root):
        result = append_task_log(root, args.task_id, args.message)
        apply_task_result(root, result)
    return _emit(args, {"task_id": result["task_id"], "path": result["new_path"]}, text=f"Updated {result['task_id']}")


def cmd_task_discovery(args: argparse.Namespace) -> int:
    from .tasks import update_task_discovery
    root = _root(args)
    with repoctl_lock(root):
        result = update_task_discovery(root, args.task_id, chosen=args.chosen or None,
                                       replace_chosen=args.replace_chosen, note=args.note)
        apply_task_result(root, result)
    return _emit(args, {"task_id": result["task_id"], "path": result["new_path"]}, text=f"Updated {result['task_id']}")


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
    with repoctl_lock(root, resource="graph-" + (target.id or "workspace")):
        snapshot, problems, meta = materialize_graph(root, target=target, rebuild=args.rebuild)
    data = {"repository": target.to_dict(), "summary": _snapshot_summary(snapshot) if snapshot else None,
            "materialization": meta.get("materialization", {})}
    if args.full:
        data["snapshot"] = snapshot.to_dict() if snapshot else None
    return _emit(args, data, problems)


class GraphQueryIntent(StrEnum):
    FILE = "file"
    SYMBOL = "symbol"
    TASK = "task"
    GENERIC = "generic"


def cmd_graph_query(args: argparse.Namespace) -> int:
    from .graph import query_graph
    from .graph_store import compact_graph_freshness, graph_materialization_freshness, graph_stale_paths, load_materialized_graph
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
            "impact_symbol", "task", "artifact", "in_file", "depth")
    result, query_problems = query_graph(snapshot, root=root, stale_paths=graph_stale_paths(freshness),
                                        **{key: getattr(args, key) for key in keys})
    problems.extend(query_problems)
    warnings = list((result or {}).get("warnings", []))
    if freshness.get("status") == "stale":
        warnings.append({"code": "graph_snapshot_stale", "message": "Sources changed since Graph was built; rebuild to refresh relations."})
    data = {"result": result if args.full or result is None else _compact_graph_query_result(result, freshness=freshness),
            "query_status": (result or {}).get("query_status", "unavailable"),
            "repository": target.to_dict(), "snapshot_digest": snapshot.snapshot_digest,
            "completeness": (result or {}).get("completeness", snapshot.completeness),
            "freshness": freshness if args.full else compact_graph_freshness(freshness),
            "materialization": meta.get("materialization", {})}
    return _emit(args, data, problems, warnings)


def cmd_graph_workflow(args: argparse.Namespace) -> int:
    from .graph_store import compact_graph_freshness, graph_materialization_freshness, load_materialized_graph
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
    result = query_graph_workflow(snapshot, root=root, target=target, route=args.route or "",
                                  symbol=args.symbol or "", in_file=args.in_file, freshness=freshness)
    if result["status"] in {"invalid", "ambiguous"}:
        problems.append(Problem("error", "graph_workflow_" + result["status"],
                                result.get("problem", "Multiple matches; select a file or qualified symbol.")))
    data = {"result": result, "query_status": result["status"], "repository": target.to_dict(),
             "completeness": snapshot.completeness,
            "materialization": meta.get("materialization", {}),
            "freshness": freshness if args.full else compact_graph_freshness(freshness)}
    warnings = [{"code": "graph_workflow_partial", "message": message} for message in result.get("warnings", [])]
    return _emit(args, data, problems, warnings)


def cmd_graph_search(args: argparse.Namespace) -> int:
    from .context import build_graph_source_evidence, compact_graph_source_evidence, render_graph_source_markdown, render_graph_source_text
    root = _root(args)
    target = _target(root, args)
    bundle, problems, meta = build_graph_source_evidence(root, target=target, query=args.query, explain=args.explain, mode=args.mode)
    data: dict[str, Any] = {"result": None, "repository": target.to_dict()}
    text = ""
    if bundle is not None:
        result = bundle.to_dict() if args.full else compact_graph_source_evidence(bundle)
        result.pop("guide", None)
        result.pop("view", None)
        if "evidence_digest" in result:
            result["result_digest"] = result.pop("evidence_digest")
        data["result"] = result
        if not args.json:
            render = render_graph_source_markdown if args.format == "markdown" else render_graph_source_text
            text = render(bundle)
    if args.full or args.explain:
        data.update(meta)
    return _emit(args, data, problems, text=text)


def cmd_knowledge(args: argparse.Namespace) -> int:
    from .knowledge_records import add_knowledge_record, check_knowledge_records, knowledge_status, query_knowledge_records, show_knowledge_record
    root = _root(args)
    target = _target(root, args)
    operation = args.command.split(".")[-1]
    warnings: list[Any] = []
    if operation == "add":
        claim = _read_text_arg(root, args.claim, args.claim_file)
        reason = _read_text_arg(root, args.reason, args.reason_file)
        with repoctl_lock(root):
            data, problems, warnings = add_knowledge_record(
                root, repo_id=target.id, kind=args.kind, claim=claim, reason=reason,
                sources=args.source, applies_to=args.applies_to, replaces=args.replaces, author=args.author)
    elif operation == "show":
        data, problems = show_knowledge_record(root, record_id=args.record_id, repo_id=target.id)
    elif operation == "check":
        data, problems = check_knowledge_records(root, repo_id=target.id)
    elif operation == "query":
        data, problems, warnings = query_knowledge_records(root, repo_id=target.id, query=args.query,
                include_history=args.include_history, limit=args.limit, explain=args.explain)
    else:
        data = knowledge_status(root, repo_id=target.id)
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
        selected.add_argument("--repo-id", help="registered repository ID")
        if workspace:
            selected.add_argument("--workspace", action="store_true", help="select workspace documents")

    def full(child):
        child.add_argument("--full", action="store_true", help="include full source data and diagnostics")

    def text_input(child, name: str, *, prefix: str = "", required: bool = False):
        pair = child.add_mutually_exclusive_group(required=required)
        pair.add_argument(f"--{prefix}{name}", default="", help=f"{name.replace('-', ' ')} text")
        pair.add_argument(f"--{prefix}{name}-file", default="", help=f"UTF-8 file containing {name.replace('-', ' ')}")

    command(groups, "version", "version", cmd_version, "Show runtime version.")
    check = command(groups, "check", "check", cmd_check, "Check Task structure and repository registration.")
    check.add_argument("--include-archived", action="store_true", help="also read archived Task documents")

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
    create.add_argument("--area", default="", help="free-form area label")
    create.add_argument("--owner", default="unassigned", help="Task owner")
    create.add_argument("--parent", default="", help="parent Task ID; lifecycle remains independent")
    create.add_argument("--repo-id", default="", help="registered repository ID")
    create.add_argument("--repo-ref", default="", help="optional descriptive repository reference")
    create.add_argument("--depends-on", action="append", default=[], help="advisory Task dependency; repeat as needed")
    create.add_argument("--start", action="store_true", help="create in doing status")
    listing = command(task, "list", "task.list", cmd_task_list, "List Task documents.")
    listing.add_argument("--include-archived", action="store_true", help="include archived Task documents")
    listing.add_argument("--status", help="filter by status")
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
        ("finish", "Complete and archive one Task."),
        ("cancel", "Cancel and archive one Task."),
        ("block", "Set a Task to blocked."),
        ("doctor", "Preview the same preparation used by finish."),
    ):
        child = command(task, name, f"task.{name}", cmd_task_transition, help)
        child.add_argument("task_id", help="Task ID or document path")
        if name in {"cancel", "block"}:
            text_input(child, "reason")
        if name == "finish":
            text_input(child, "summary")
            child.add_argument("--knowledge-kind", choices=("decision", "failure_mode", "invariant"), default="",
                               help="also save a reusable Knowledge record")
            text_input(child, "claim", prefix="knowledge-")
            text_input(child, "reason", prefix="knowledge-")
            child.add_argument("--knowledge-applies-to", action="append", default=[], help="repository-relative file; repeat as needed")
            child.add_argument("--knowledge-replaces", action="append", default=[], help="replaced Knowledge record ID; repeat as needed")
            child.add_argument("--knowledge-author", default="", help="record author")
    log = task.add_parser("log", help="Add an execution note.")
    log_sub = log.add_subparsers(dest="task_log_command", required=True)
    append = command(log_sub, "append", "task.log.append", cmd_task_log, "Append authored text to Execution Log.")
    append.add_argument("task_id", help="Task ID or document path")
    append.add_argument("message", help="log text")
    discovery = task.add_parser("discovery", help="Record declared scope.")
    discovery_sub = discovery.add_subparsers(dest="task_discovery_command", required=True)
    add = command(discovery_sub, "add", "task.discovery.add", cmd_task_discovery, "Update declared Chosen files and notes.")
    add.add_argument("task_id", help="Task ID or document path")
    add.add_argument("--chosen", action="append", default=[], help="declared file; repeat as needed")
    add.add_argument("--replace-chosen", action="append", help="replace the declared set; repeat as needed")
    add.add_argument("--note", default="", help="scope note")

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
    scope(build)
    full(build)
    build.add_argument("--rebuild", action="store_true", help="rebuild provider caches")
    query = command(graph, "query", "graph.query", cmd_graph_query, "Query indexed files, symbols and relationships.")
    scope(query)
    full(query)
    for name in ("file", "symbol", "callers-of", "callees-of", "impact-file", "impact-symbol", "task", "artifact", "in-file"):
        query.add_argument(f"--{name}", default="", help=f"{name.replace('-', ' ')} selector")
    query.add_argument("--import", dest="import_ref", default="", help="import selector")
    query.add_argument("--depth", type=int, default=1, help="relationship traversal depth")
    workflow = command(graph, "workflow", "graph.workflow", cmd_graph_workflow, "Locate route or symbol implementation and relevant tests.")
    scope(workflow, workspace=False)
    full(workflow)
    selector = workflow.add_mutually_exclusive_group(required=True)
    selector.add_argument("--route", help="HTTP route literal")
    selector.add_argument("--symbol", help="symbol name")
    workflow.add_argument("--in-file", default="", help="repository-relative entrypoint file")
    search = command(graph, "search", "graph.search", cmd_graph_search, "Search source-linked code and documents.")
    scope(search)
    full(search)
    search.add_argument("query", help="search text")
    search.add_argument("--mode", default="", help="search intent, such as code-location or past-decision")
    search.add_argument("--explain", action="store_true", help="include retrieval details")
    search.add_argument("--format", choices=("text", "markdown"), default="text", help="human-readable format")

    knowledge = group("knowledge", "Store and query reusable claims with source citations.")
    for name in ("add", "show", "query", "check", "status"):
        child = command(knowledge, name, f"knowledge.{name}", cmd_knowledge, f"{name.title()} Knowledge records.")
        scope(child)
        if name == "add":
            child.add_argument("--kind", choices=("decision", "failure_mode", "invariant"), default="decision", help="record kind")
            text_input(child, "claim", required=True)
            text_input(child, "reason", required=True)
            child.add_argument("--source", action="append", required=True, help="workspace-relative source; repeat as needed")
            child.add_argument("--applies-to", action="append", default=[], help="repository-relative file; repeat as needed")
            child.add_argument("--replaces", action="append", default=[], help="replaced record ID; repeat as needed")
            child.add_argument("--author", default="", help="record author")
        elif name == "show":
            child.add_argument("record_id", help="Knowledge record ID")
        elif name == "query":
            child.add_argument("query", help="search text")
            child.add_argument("--include-history", action="store_true", help="include replaced records")
            child.add_argument("--limit", type=int, default=10, help="maximum returned records")
            child.add_argument("--explain", action="store_true", help="include match details")
            full(child)
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
        data = {"finish_ready": False} if args.command == "task.doctor" else {}
        _emit(args, data, [problem])
        return 2


from .graph_structured_relations import STRUCTURED_EDGE_KIND

def _compact_graph_query_result(result: dict[str, Any], *, freshness: dict[str, Any] | None = None) -> dict[str, Any]:
    from .graph import compact_relationship_candidates
    nodes = result.get("nodes") if isinstance(result.get("nodes"), list) else []
    edges = result.get("edges") if isinstance(result.get("edges"), list) else []
    matches = result.get("matches") if isinstance(result.get("matches"), list) else []
    paths = result.get("paths") if isinstance(result.get("paths"), list) else []
    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    continuations = result.get("continuations") if isinstance(result.get("continuations"), list) else []
    relationship_candidate_projection = compact_relationship_candidates(result.get("relationship_candidates"))
    node_summaries = {
        str(node.get("id") or ""): _compact_graph_node(node, include_id=False)
        for node in nodes
        if isinstance(node, dict) and str(node.get("id") or "")
    }
    query_intent = _graph_query_intent(result.get("query"))
    displayed_matches = [match for match in matches[:3] if isinstance(match, dict)]
    match_keys = {
        key
        for match in displayed_matches
        if (key := _graph_summary_selector_key(match)) is not None
    }
    continuation_by_key = _compact_graph_continuation_index(continuations)
    ordered_paths = _dedupe_compact_graph_paths(
        sorted(
            [path for path in paths if isinstance(path, dict)],
            key=lambda path: _compact_graph_path_key(path, query_intent=query_intent),
        )
    )
    selected_paths, selected_continuation_keys = _select_compact_graph_paths(
        ordered_paths,
        continuation_by_key=continuation_by_key,
        match_keys=match_keys,
        path_limit=3,
        continuation_limit=3,
    )
    completeness = result.get("completeness") if isinstance(result.get("completeness"), dict) else {}
    displayed_paths = [_compact_graph_path(path, completeness=completeness, freshness=freshness or {}) for path in selected_paths]
    relation_edges = [
        edge
        for edge in edges
        if isinstance(edge, dict) and str(edge.get("kind") or "") not in {"DEFINES", "ANCHORS"}
    ]
    relation_edges.sort(key=lambda edge: _compact_graph_relation_key(edge, query_intent=query_intent))
    relation_edges = _dedupe_compact_graph_edges(relation_edges)
    selected_relations: list[dict[str, Any]] = []
    if not displayed_paths:
        selected_relations, selected_continuation_keys = _select_compact_graph_relations(
            relation_edges,
            node_summaries=node_summaries,
            continuation_by_key=continuation_by_key,
            match_keys=match_keys,
            relation_limit=3,
            continuation_limit=3,
        )
    for key in sorted(match_keys):
        if len(selected_continuation_keys) >= 3:
            break
        if key in continuation_by_key and key not in selected_continuation_keys:
            selected_continuation_keys.append(key)
    displayed_continuations = _compact_graph_continuations(
        selected_continuation_keys,
        continuation_by_key=continuation_by_key,
    )
    displayed_relations = [
        _compact_graph_relation(edge, node_summaries, completeness=completeness, freshness=freshness or {})
        for edge in selected_relations
    ]
    compact = {
        "query": result.get("query", {}),
        "query_status": result.get("query_status", "unavailable"),
        "result_digest": result.get("result_digest", ""),
        "matches": displayed_matches[:3],
        "match_count": len(matches),
        "matches_truncated": len(matches) > len(displayed_matches),
        "candidates": [candidate for candidate in candidates[:3] if isinstance(candidate, dict)],
        "candidate_count": len(candidates),
        "candidates_truncated": len(candidates) > 3,
        "relationship_candidates": relationship_candidate_projection["items"],
        "relationship_candidate_count": relationship_candidate_projection["total_count"],
        "relationship_candidates_truncated": relationship_candidate_projection["truncated"],
        "component_crossings": [
            crossing
            for crossing in result.get("component_crossings", [])[:3]
            if isinstance(crossing, dict)
        ],
        "component_crossing_count": int(result.get("component_crossing_count") or 0),
        "component_crossings_truncated": bool(result.get("component_crossings_truncated"))
        or int(result.get("component_crossing_count") or 0) > 3,
        "paths": displayed_paths,
        "path_count": len(ordered_paths),
        "paths_truncated": len(displayed_paths) < len(ordered_paths),
        "continuations": displayed_continuations,
    }
    if not compact["paths"]:
        compact["relations"] = displayed_relations
        compact["relation_count"] = len(relation_edges)
        compact["relations_truncated"] = len(selected_relations) < len(relation_edges)
    return compact


_GRAPH_QUERY_INTENT_BY_TYPE = {
    "file": GraphQueryIntent.FILE,
    "impact_file": GraphQueryIntent.FILE,
    "symbol": GraphQueryIntent.SYMBOL,
    "callers_of": GraphQueryIntent.SYMBOL,
    "callees_of": GraphQueryIntent.SYMBOL,
    "impact_symbol": GraphQueryIntent.SYMBOL,
    "task": GraphQueryIntent.TASK,
    "artifact": GraphQueryIntent.TASK,
}

_GRAPH_RELATION_ORDER = {
    GraphQueryIntent.FILE: (
        "TESTS_FILE",
        "IMPORTS_FILE",
        STRUCTURED_EDGE_KIND,
        "KNOWLEDGE_APPLIES_TO",
        "TASK_REFERENCES_FILE",
        "TASK_DOCUMENTED_BY",
        "KNOWLEDGE_SOURCED_FROM",
        "CALLS",
        "RESOLVES_TO",
        "DECLARES_IMPORT",
        "CONTAINS",
    ),
    GraphQueryIntent.SYMBOL: (
        "CALLS",
        "TESTS_FILE",
        "IMPORTS_FILE",
        STRUCTURED_EDGE_KIND,
        "KNOWLEDGE_APPLIES_TO",
        "TASK_REFERENCES_FILE",
        "TASK_DOCUMENTED_BY",
        "KNOWLEDGE_SOURCED_FROM",
        "RESOLVES_TO",
        "DECLARES_IMPORT",
        "CONTAINS",
    ),
    GraphQueryIntent.TASK: (
        "TASK_DOCUMENTED_BY",
        "TASK_REFERENCES_FILE",
        "KNOWLEDGE_SOURCED_FROM",
        "KNOWLEDGE_APPLIES_TO",
        "TESTS_FILE",
        "IMPORTS_FILE",
        STRUCTURED_EDGE_KIND,
        "CALLS",
        "RESOLVES_TO",
        "DECLARES_IMPORT",
        "CONTAINS",
    ),
    GraphQueryIntent.GENERIC: (
        "RESOLVES_TO",
        "DECLARES_IMPORT",
        STRUCTURED_EDGE_KIND,
        "CONTAINS",
        "TESTS_FILE",
        "IMPORTS_FILE",
        "CALLS",
        "KNOWLEDGE_APPLIES_TO",
        "TASK_DOCUMENTED_BY",
        "TASK_REFERENCES_FILE",
        "KNOWLEDGE_SOURCED_FROM",
    ),
}


def _graph_query_intent(query: Any) -> GraphQueryIntent:
    if not isinstance(query, dict):
        return GraphQueryIntent.GENERIC
    return _GRAPH_QUERY_INTENT_BY_TYPE.get(str(query.get("type") or ""), GraphQueryIntent.GENERIC)


def _graph_relation_priority(kind: str, *, query_intent: GraphQueryIntent) -> int:
    order = _GRAPH_RELATION_ORDER[query_intent]
    try:
        return order.index(kind)
    except ValueError:
        return len(order)


def _compact_graph_path_key(path: dict[str, Any], *, query_intent: GraphQueryIntent) -> tuple[int, str, str, str]:
    edge = str(path.get("edge") or "")
    return (
        _graph_relation_priority(edge, query_intent=query_intent),
        edge,
        str(path.get("from") or ""),
        str(path.get("to") or ""),
    )


def _compact_graph_continuation_index(continuations: list[Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in continuations:
        if not isinstance(item, dict):
            continue
        key = _graph_selector_key(item.get("selector"))
        if key is not None and key not in by_key:
            by_key[key] = item
    return by_key


def _graph_relation_endpoints(item: dict[str, Any]) -> tuple[str, str]:
    def identity(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("id") or _graph_summary_selector_key(value) or "")
        return str(value or "")

    return identity(item.get("from")), identity(item.get("to"))


def _dedupe_compact_graph_paths(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    direct_test_endpoints = {
        _graph_relation_endpoints(path)
        for path in paths
        if str(path.get("edge") or "") == "TESTS_FILE"
    }
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in paths:
        edge = str(path.get("edge") or "")
        endpoints = _graph_relation_endpoints(path)
        if edge == "IMPORTS_FILE" and endpoints in direct_test_endpoints:
            continue
        key = (edge, *endpoints)
        if key not in seen:
            seen.add(key)
            selected.append(path)
    return selected


def _dedupe_compact_graph_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    direct_test_endpoints = {
        (str(edge.get("from") or ""), str(edge.get("to") or ""))
        for edge in edges
        if str(edge.get("kind") or "") == "TESTS_FILE"
    }
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        kind = str(edge.get("kind") or "")
        endpoints = (str(edge.get("from") or ""), str(edge.get("to") or ""))
        if kind == "IMPORTS_FILE" and endpoints in direct_test_endpoints:
            continue
        key = (kind, *endpoints)
        if key not in seen:
            seen.add(key)
            selected.append(edge)
    return selected


def _relation_continuation_keys(
    endpoints: list[Any],
    *,
    continuation_by_key: dict[tuple[str, str, str], dict[str, Any]],
    match_keys: set[tuple[str, str, str]],
) -> list[tuple[str, str, str]] | None:
    keys: list[tuple[str, str, str]] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        key = _graph_summary_selector_key(endpoint)
        if key is None:
            return None
        if key in match_keys:
            continue
        if key not in continuation_by_key:
            return None
        if key not in keys:
            keys.append(key)
    return keys


def _select_compact_graph_paths(
    paths: list[dict[str, Any]],
    *,
    continuation_by_key: dict[tuple[str, str, str], dict[str, Any]],
    match_keys: set[tuple[str, str, str]],
    path_limit: int,
    continuation_limit: int,
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    selected_paths: list[dict[str, Any]] = []
    selected_keys: list[tuple[str, str, str]] = []
    for path in paths:
        keys = _relation_continuation_keys(
            [path.get("from"), path.get("to")],
            continuation_by_key=continuation_by_key,
            match_keys=match_keys,
        )
        if keys is None:
            continue
        new_keys = [key for key in keys if key not in selected_keys]
        if len(selected_keys) + len(new_keys) > continuation_limit:
            continue
        selected_paths.append(path)
        selected_keys.extend(new_keys)
        if len(selected_paths) >= path_limit:
            break
    return selected_paths, selected_keys


def _select_compact_graph_relations(
    edges: list[dict[str, Any]],
    *,
    node_summaries: dict[str, dict[str, Any]],
    continuation_by_key: dict[tuple[str, str, str], dict[str, Any]],
    match_keys: set[tuple[str, str, str]],
    relation_limit: int,
    continuation_limit: int,
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    selected_edges: list[dict[str, Any]] = []
    selected_keys: list[tuple[str, str, str]] = []
    for edge in edges:
        endpoints = [
            node_summaries.get(str(edge.get("from") or ""), {}),
            node_summaries.get(str(edge.get("to") or ""), {}),
        ]
        keys = _relation_continuation_keys(
            endpoints,
            continuation_by_key=continuation_by_key,
            match_keys=match_keys,
        )
        if keys is None:
            continue
        new_keys = [key for key in keys if key not in selected_keys]
        if len(selected_keys) + len(new_keys) > continuation_limit:
            continue
        selected_edges.append(edge)
        selected_keys.extend(new_keys)
        if len(selected_edges) >= relation_limit:
            break
    return selected_edges, selected_keys


def _compact_graph_continuations(
    keys: list[tuple[str, str, str]],
    *,
    continuation_by_key: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "selector": continuation_by_key[key].get("selector", {}),
            "query_types": continuation_by_key[key].get("query_types", []),
            "actions": continuation_by_key[key].get("actions", []),
        }
        for key in keys
    ]


def _compact_graph_relation_key(edge: dict[str, Any], *, query_intent: GraphQueryIntent) -> tuple[int, str, str, str]:
    kind = str(edge.get("kind") or "")
    return (
        _graph_relation_priority(kind, query_intent=query_intent),
        kind,
        str(edge.get("from") or ""),
        str(edge.get("to") or ""),
    )


def _graph_selector_key(selector: Any) -> tuple[str, str, str] | None:
    if not isinstance(selector, dict):
        return None
    kind = str(selector.get("kind") or "")
    value = str(selector.get("value") or "")
    if not kind or not value:
        return None
    return kind, value, str(selector.get("in_file") or "")


def _graph_summary_selector_key(summary: dict[str, Any]) -> tuple[str, str, str] | None:
    kind = str(summary.get("kind") or "")
    if kind == "file":
        selector = {"kind": "file", "value": summary.get("path")}
    elif kind == "symbol":
        selector = {
            "kind": "symbol",
            "value": summary.get("qualified_name") or summary.get("name"),
            "in_file": summary.get("path"),
        }
    elif kind == "import_ref":
        selector = {"kind": "import", "value": summary.get("raw_import")}
    elif kind == "task":
        selector = {"kind": "task", "value": summary.get("task_id")}
    elif kind == "artifact":
        selector = {"kind": "document", "value": summary.get("path")}
    elif kind == "document":
        selector = {"kind": "document", "value": summary.get("path")}
    elif kind == "knowledge":
        selector = {"kind": "knowledge_record", "value": summary.get("record_id")}
    else:
        return None
    return _graph_selector_key(selector)


def _compact_graph_node(node: dict[str, Any], *, include_id: bool = True) -> dict[str, Any]:
    summary: dict[str, Any] = {"kind": node.get("kind", "")}
    if include_id:
        summary["id"] = node.get("id", "")
    identity = node.get("identity") if isinstance(node.get("identity"), dict) else {}
    facts = node.get("facts") if isinstance(node.get("facts"), dict) else {}
    provider = facts.get("provider") if isinstance(facts.get("provider"), dict) else {}
    for key, value in (
        ("path", identity.get("path")),
        ("task_id", identity.get("task_id")),
        ("raw_import", identity.get("raw_import")),
        ("name", provider.get("name")),
        ("qualified_name", provider.get("qualified_name")),
        ("symbol_kind", provider.get("kind")),
        ("record_id", identity.get("record_id")),
        ("status", (facts.get("record") or {}).get("status") if isinstance(facts.get("record"), dict) else None),
        ("knowledge_kind", (facts.get("record") or {}).get("kind") if isinstance(facts.get("record"), dict) else None),
    ):
        if value not in (None, ""):
            summary[key] = value
    return summary


def _compact_graph_path(path: dict[str, Any], *, completeness: dict[str, Any], freshness: dict[str, Any]) -> dict[str, Any]:
    compact = dict(path)
    for key in ("from", "to"):
        endpoint = compact.get(key)
        if isinstance(endpoint, dict):
            compact[key] = {name: value for name, value in endpoint.items() if name != "id"}
    source = compact.get("source") if isinstance(compact.get("source"), dict) else {}
    compact["evidence"] = _compact_relation_evidence(
        edge=str(compact.get("edge") or ""),
        assertion=str(source.get("assertion") or ""),
        provider=str(source.get("provider") or ""),
        facts=source.get("facts") if isinstance(source.get("facts"), dict) else {},
        endpoints=[compact.get("from"), compact.get("to")],
        completeness=completeness,
        freshness=freshness,
    )
    compact.pop("source", None)
    return compact


def _compact_graph_relation(
    edge: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    *,
    completeness: dict[str, Any],
    freshness: dict[str, Any],
) -> dict[str, Any]:
    source_node = nodes.get(str(edge.get("from") or ""), {"id": edge.get("from", "")})
    target_node = nodes.get(str(edge.get("to") or ""), {"id": edge.get("to", "")})
    relation: dict[str, Any] = {
        "from": source_node,
        "edge": edge.get("kind", ""),
        "to": target_node,
        "evidence": _compact_relation_evidence(
            edge=str(edge.get("kind") or ""),
            assertion=str(edge.get("assertion") or ""),
            provider=str(edge.get("source") or ""),
            facts=edge.get("facts") if isinstance(edge.get("facts"), dict) else {},
            endpoints=[source_node, target_node],
            completeness=completeness,
            freshness=freshness,
        ),
    }
    if isinstance(edge.get("facts"), dict) and edge["facts"]:
        relation["facts"] = edge["facts"]
    return relation


def _compact_relation_evidence(
    *,
    edge: str,
    assertion: str,
    provider: str,
    facts: dict[str, Any],
    endpoints: list[Any],
    completeness: dict[str, Any],
    freshness: dict[str, Any],
) -> dict[str, Any]:
    capability_by_edge = {
        "CALLS": "calls",
        "DECLARES_IMPORT": "imports",
        "IMPORTS_FILE": "imports",
        "RESOLVES_TO": "imports",
        "TESTS_FILE": "imports",
        STRUCTURED_EDGE_KIND: "structured_relations",
        "TASK_REFERENCES_FILE": "task_history",
        "TASK_DOCUMENTED_BY": "task_history",
        "KNOWLEDGE_APPLIES_TO": "knowledge",
        "KNOWLEDGE_SOURCED_FROM": "knowledge",
    }
    capabilities = completeness.get("capabilities") if isinstance(completeness.get("capabilities"), dict) else {}
    confidence = str(facts.get("confidence") or "unknown")
    from .graph_store import graph_stale_paths
    stale_paths = graph_stale_paths(freshness)
    changed_root_paths = {str(path) for path in freshness.get("changed_root_paths", []) if str(path)}
    endpoint_paths = {
        str(endpoint.get("path") or "")
        for endpoint in endpoints
        if isinstance(endpoint, dict) and str(endpoint.get("path") or "")
    }
    freshness_status = "stale" if endpoint_paths & stale_paths else "current"
    root_evidence_edges = {
        "TASK_REFERENCES_FILE",
        "TASK_DOCUMENTED_BY",
        "KNOWLEDGE_APPLIES_TO",
        "KNOWLEDGE_SOURCED_FROM",
    }
    if endpoint_paths & changed_root_paths or (freshness.get("root_evidence_changed") and edge in root_evidence_edges):
        freshness_status = "stale"
    recorded_freshness = str(facts.get("freshness") or "")
    if recorded_freshness == "stale":
        freshness_status = "stale"
    elif recorded_freshness == "current" and freshness_status != "stale":
        freshness_status = "current"
    if str(freshness.get("status") or "") not in {"current", "stale"}:
        freshness_status = "stale" if recorded_freshness == "stale" else "unknown"
    return {
        "type": str(facts.get("evidence_type") or edge.casefold()),
        "assertion": assertion,
        "provider": provider,
        "confidence": confidence,
        "completeness": str(capabilities.get(capability_by_edge.get(edge, "file_inventory")) or completeness.get("status") or "partial"),
        "freshness": freshness_status,
    }
