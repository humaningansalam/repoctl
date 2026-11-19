from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.repoctl.cli import main


@pytest.mark.parametrize("child", [False, True])
def test_finish_knowledge_check_and_query_reference_final_task_bytes(
    tmp_path: Path, monkeypatch, capsys, child: bool
) -> None:
    (tmp_path / "docs/tasks").mkdir(parents=True)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    def run(arguments: list[str]) -> dict:
        assert main([*arguments, "--json"]) == 0
        return json.loads(capsys.readouterr().out)["data"]

    parent_args: list[str] = []
    if child:
        parent = run(["task", "create", "Parent remains active", "--start"])
        parent_args = ["--parent", parent["task_id"]]
    task = run(["task", "create", "Final result", "--start", *parent_args])
    completed = run(
        [
            "task",
            "finish",
            task["task_id"],
            "--knowledge-claim",
            "Completion keeps exact source bytes",
            "--knowledge-reason",
            "The final document is the saved result",
        ]
    )
    content = (tmp_path / completed["path"]).read_bytes()
    record = json.loads((tmp_path / completed["knowledge_path"]).read_bytes())
    assert record["source_refs"] == [
        {
            "kind": "task_artifact",
            "path": completed["path"],
            "content_sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        }
    ]
    checked = run(["knowledge", "check", "--workspace"])
    assert checked["records"][0]["source_resolutions"][0]["status"] == "current"
    queried = run(["knowledge", "query", "exact source bytes", "--workspace"])
    assert (
        queried["results"][0]["record"]["source_resolutions"][0]["status"] == "current"
    )
    assert (tmp_path / completed["path"]).read_bytes() == content
    if child:
        assert (tmp_path / parent["path"]).exists()
