from __future__ import annotations

import json
from pathlib import Path

from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.repository.test_repositories import init_repo, write_settings


def _write_context_docs(root: Path) -> None:
    (root / "AGENTS.md").write_text(
        "# Workspace rules\n\nRead the active PRD before changing product code.\n",
        encoding="utf-8",
    )
    (root / "docs/adr").mkdir(parents=True, exist_ok=True)
    (root / "docs/contracts").mkdir(parents=True, exist_ok=True)
    (root / "docs/workflows").mkdir(parents=True, exist_ok=True)
    (root / "docs/PRD.md").write_text(
        "# PRD\n\n## Graph and Knowledge\n\nGraph retrieves source-bound implementation evidence; Knowledge stores reviewed claims and their reasons.\n",
        encoding="utf-8",
    )
    (root / "docs/contracts/repoctl-graph-contract.md").write_text(
        "# repoctl Graph contract\n\nGraph is a read-only derived evidence snapshot. Sources remain code, repository registration, and Task documents.\n",
        encoding="utf-8",
    )
    (root / "docs/contracts/repoctl-evidence-contract.md").write_text(
        "# repoctl Graph evidence contract\n\nGraph evidence retrieval returns read-only, source-linked results. It does not replace original code, Task documents or product code.\n",
        encoding="utf-8",
    )
    (root / "docs/contracts/repoctl-module-boundaries.md").write_text(
        "# repoctl module boundaries\n\n## Derived layer rules\n\nGraph and Knowledge are derived navigation and reusable judgment, not Task or product repository authority.\n",
        encoding="utf-8",
    )
    (root / "docs/workflows/generated.md").write_text(
        "# Workflow\n\nGenerated output is not an authority.\n", encoding="utf-8"
    )


def _setup_context_workspace(root: Path, monkeypatch) -> Path:
    write_workspace(root)
    _write_context_docs(root)
    repo = root / "repos"
    init_repo(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)
    return repo


def _setup_context_multirepo_workspace(root: Path, monkeypatch) -> None:
    write_workspace(root)
    _write_context_docs(root)
    init_repo(root / "repos/web")
    init_repo(root / "repos/api")
    write_settings(
        root,
        {
            "repositories": [
                {"id": "web", "path": "repos/web"},
                {"id": "api", "path": "repos/api"},
            ]
        },
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)
