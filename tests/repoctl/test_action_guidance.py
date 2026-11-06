from __future__ import annotations

from tools.repoctl.action_guidance import prioritize_next_actions


def test_source_continuation_precedes_optional_graph_build_and_repeat() -> None:
    payload = {
        "ok": True,
        "command": "graph.search",
        "data": {
            "result": {
                "groups": {
                    "likely_change_surface": [
                        {"source_ref": {"kind": "code", "path": "repos/app.py"}},
                    ],
                },
            },
        },
        "problems": [],
        "next_actions": [
            {"kind": "graph_build", "command": "./scripts/repoctl graph build --repo-id main --json"},
            {"kind": "graph_search_resume", "command": "./scripts/repoctl graph search app --repo-id main --json"},
        ],
    }
    actions = prioritize_next_actions(payload)
    assert [item["kind"] for item in actions] == [
        "source_continuation", "graph_build", "graph_search_resume",
    ]
    assert [item["priority"] for item in actions] == [
        "required", "recommended", "conditional",
    ]
    assert actions[0]["path"] == "repos/app.py"
    assert "priority" not in payload["next_actions"][0]


def test_broken_graph_without_source_still_requires_recovery() -> None:
    actions = prioritize_next_actions({
        "ok": False,
        "command": "graph.search",
        "data": {"result": {"groups": {"likely_change_surface": []}}},
        "problems": [{"severity": "error", "code": "graph_materialization_invalid"}],
        "next_actions": [{"kind": "graph_rebuild", "label": "Rebuild Graph"}],
    })
    assert len(actions) == 1
    assert actions[0]["priority"] == "required"


def test_doctor_ready_to_finish_does_not_require_handoff_binding() -> None:
    actions = prioritize_next_actions({
        "ok": True,
        "command": "task.doctor",
        "data": {"finish_ready": True, "task_id": "T-20261105010101Z"},
        "problems": [],
        "next_actions": [
            {"kind": "task_handoff_bind", "command": "./scripts/repoctl task handoff bind T-20261105010101Z --json"},
        ],
    })
    assert [(item["kind"], item["priority"]) for item in actions] == [
        ("task_finish", "recommended"), ("task_handoff_bind", "conditional"),
    ]


def test_started_task_prioritizes_chosen_scope_over_pause_only_handoff() -> None:
    actions = prioritize_next_actions({
        "ok": True,
        "command": "task.create",
        "data": {},
        "problems": [],
        "next_actions": [
            {"label": "Replace the generated Handoff with task-specific restart instructions", "path": "docs/tasks/T.md"},
            {"kind": "discovery_input", "path": "docs/tasks/T.md"},
        ],
    })
    assert [item["priority"] for item in actions] == ["required", "conditional"]
    assert actions[0]["kind"] == "discovery_input"


def test_unsafe_or_nonexistent_source_is_not_an_actionable_continuation() -> None:
    actions = prioritize_next_actions({
        "ok": True,
        "command": "graph.search",
        "data": {"result": {"groups": {"likely_change_surface": [
            {"source_ref": {"path": "../outside.py"}},
            {"source_ref": {"path": ""}},
        ]}}},
        "problems": [],
        "next_actions": [],
    })
    assert actions == []
