from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.repoctl.cli import main
from tools.repoctl.io import RepoctlError
from tools.repoctl.upgrade import apply_upgrade, plan_upgrade, write_plan
from tests.repoctl.test_upgrade import write_source, write_workspace
from tests.repoctl.repository.test_repositories import write_settings


def _snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file() and not path.is_symlink()
    }


def _split(root: Path) -> None:
    marker = Path(__file__).resolve().parents[1] / "fixtures/template-contract-1.0.0/docs/template-contract.json"
    (root / "docs/template-contract.json").write_bytes(marker.read_bytes())
    (root / "scripts/repoctl").write_text(
        '#!/usr/bin/env bash\nREPOCTL_HOME=/installed/repoctl\n'
        'exec env REPOCTL_WORKSPACE_ROOT="$PWD" "$REPOCTL_HOME/scripts/repoctl" "$@"\n',
        encoding="utf-8",
    )


def test_split_upgrade_plan_and_old_plan_apply_rejected_with_zero_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    workspace = tmp_path / "adopter"
    source = tmp_path / "old-source"
    old_plan = tmp_path / "old-plan.json"
    new_plan = tmp_path / "must-not-exist.json"
    write_workspace(workspace)
    write_source(source)
    # The historical plan was legal when its target really embedded runtime.
    write_plan(old_plan, plan_upgrade(workspace, source=source))
    _split(workspace)
    write_settings(workspace, {"debug_mode": True})
    before = _snapshot(workspace)
    monkeypatch.setenv("REPOCTL_WORKSPACE_ROOT", str(workspace))

    assert main(["upgrade", "plan", "--workspace-root", str(workspace), "--from", str(source),
                 "--output", str(new_plan), "--json"]) == 2
    plan_result = json.loads(capsys.readouterr().out)
    assert plan_result["problems"][0]["code"] == "upgrade_split_workspace_forbidden"
    assert not new_plan.exists()
    assert main(["upgrade", "apply", "--workspace-root", str(workspace),
                 "--plan-file", str(old_plan), "--json"]) == 2
    apply_result = json.loads(capsys.readouterr().out)
    assert apply_result["problems"][0]["code"] == "upgrade_split_workspace_forbidden"
    assert _snapshot(workspace) == before
    assert not (workspace / "docs/tasks/.repoctl-state/debug/events.jsonl").exists()
    assert (workspace / "scripts/repoctl").read_text().startswith("#!/usr/bin/env bash")


def test_split_launcher_rejected_even_without_contract_marker(tmp_path: Path) -> None:
    workspace = tmp_path / "adopter"
    source = tmp_path / "source"
    write_workspace(workspace)
    write_source(source)
    _split(workspace)
    (workspace / "docs/template-contract.json").unlink()
    before = _snapshot(workspace)
    with pytest.raises(RepoctlError) as error:
        plan_upgrade(workspace, source=source)
    assert error.value.code == "upgrade_split_workspace_forbidden"
    assert _snapshot(workspace) == before


def test_document_only_target_cannot_become_runtime_install(tmp_path: Path) -> None:
    workspace = tmp_path / "adopter"
    source = tmp_path / "source"
    write_workspace(workspace)
    write_source(source)
    (workspace / "tools/repoctl/cli.py").unlink()
    before = _snapshot(workspace)
    with pytest.raises(RepoctlError) as error:
        plan_upgrade(workspace, source=source)
    assert error.value.code == "upgrade_target_not_embedded"
    assert _snapshot(workspace) == before
