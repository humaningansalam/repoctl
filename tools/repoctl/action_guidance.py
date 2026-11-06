"""Prioritized, non-executing guidance for repoctl's machine and human readers."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


PRIORITY_ORDER = {"required": 0, "recommended": 1, "conditional": 2}
GRAPH_MAINTENANCE = {
    "graph_build", "graph_rebuild", "graph_refresh",
    "knowledge_rebuild", "completion_catalogue_rebuild",
}


def _first_source_path(data: dict[str, Any]) -> str:
    result = data.get("result")
    if not isinstance(result, dict):
        return ""
    groups = result.get("groups")
    if not isinstance(groups, dict):
        return ""
    for group_name in ("likely_change_surface", "must_read", "tests_and_verification"):
        group = groups.get(group_name)
        if not isinstance(group, list):
            continue
        for item in group:
            source = item.get("source_ref") if isinstance(item, dict) else None
            path = source.get("path") if isinstance(source, dict) else None
            if not isinstance(path, str) or not path or "\x00" in path:
                continue
            rel = PurePosixPath(path)
            if not rel.is_absolute() and ".." not in rel.parts and path != ".":
                return path
    return ""


def prioritize_next_actions(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Classify existing guidance; add only a source-linked continuation or safe finish hint.

    An action does not execute or establish Task scope. A usable source result
    does not require rebuilding optional Graph state before source inspection.
    """
    raw_actions = envelope.get("next_actions")
    actions = [dict(action) for action in raw_actions if isinstance(action, dict)] if isinstance(raw_actions, list) else []
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    command = str(envelope.get("command") or "")
    source_path = _first_source_path(data) if command == "graph.search" else ""
    if source_path and not any(action.get("kind") == "source_continuation" for action in actions):
        actions.insert(
            0,
            {
                "label": "Inspect this source-linked file before editing",
                "path": source_path,
                "kind": "source_continuation",
                "source": "data.result.groups",
            },
        )

    if command == "task.doctor" and data.get("finish_ready") is True:
        task_id = str(data.get("task_id") or "")
        if task_id.startswith("T-") and not any(action.get("kind") == "task_finish" for action in actions):
            actions.insert(
                0,
                {
                    "label": "Finish the task once product acceptance has been checked",
                    "command": f"./scripts/repoctl task finish {task_id} --json",
                    "kind": "task_finish",
                    "source": "data.finish_ready",
                },
            )

    has_error = bool(envelope.get("problems"))
    for action in actions:
        if action.get("priority") in PRIORITY_ORDER:
            continue
        kind = str(action.get("kind") or "")
        label = str(action.get("label") or "")
        source = str(action.get("source") or "")
        if kind in {"task_handoff_bind", "graph_search_resume"} or (
            "generated Handoff" in label or source == "data.generated_handoff"
        ):
            priority = "conditional"
        elif kind in GRAPH_MAINTENANCE:
            priority = "recommended" if source_path else ("required" if has_error else "recommended")
        elif kind in {"context_doc_review", "source_continuation", "discovery_input", "task_scope_review", "baseline_ownership_resolution"}:
            priority = "required"
        elif kind == "task_finish":
            priority = "recommended"
        else:
            priority = "required" if has_error else "recommended"
        action["priority"] = priority

    return sorted(actions, key=lambda action: PRIORITY_ORDER[action["priority"]])
