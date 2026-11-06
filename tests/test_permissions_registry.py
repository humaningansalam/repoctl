"""The split runtime must not install agent-specific permission grants."""
from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_does_not_ship_agent_permission_overrides() -> None:
    manifest = json.loads((REPO_ROOT / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    owned = {*manifest["create_paths"], *manifest["replace_paths"]}
    assert not (REPO_ROOT / ".claude/settings.json").exists()
    assert not (REPO_ROOT / ".cursor").exists()
    assert not (REPO_ROOT / "CLAUDE.md").exists()
    assert not any(path.startswith((".claude/", ".cursor/")) for path in owned)
    assert "CLAUDE.md" not in owned


def test_runtime_ignore_is_scoped_to_dev_artifacts_not_adopter_permissions() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for generated in (".venv/", ".pytest_cache/", "__pycache__/", "dist/"):
        assert generated in gitignore
    assert ".claude/settings.local.json" not in gitignore
