"""Independent split-runtime integration: pinned template fixture, no combined checkout."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path

from tools.repoctl.release import build_release_archive


ROOT = Path(__file__).resolve().parents[2]
PINNED_TEMPLATE = ROOT / "tests/fixtures/template-contract-1.0.0"
CONFORMANCE = ROOT / "fixtures/conformance"


def _extract(source: Path, destination: Path) -> Path:
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        assert members
        assert all(member.isfile() and not Path(member.name).is_absolute()
                   and ".." not in Path(member.name).parts for member in members)
        archive.extractall(destination, filter="data")
    return destination / Path(members[0].name).parts[0]


def _run(runtime: Path, workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("PYTHON", None)
    env.pop("VIRTUAL_ENV", None)
    env.update(REPOCTL_WORKSPACE_ROOT=str(workspace), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [str(runtime / "scripts/repoctl"), *args],
        cwd=workspace, env=env, capture_output=True, text=True, check=False, timeout=30,
    )


def test_independent_runtime_archive_reproducible_and_ownership_clean(tmp_path: Path) -> None:
    manifest = json.loads((ROOT / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert manifest["version"] == version
    assert manifest["package"] == "repoctl"
    first = build_release_archive(ROOT, tmp_path / "first")
    second = build_release_archive(ROOT, tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).digest()
    runtime = _extract(first, tmp_path / "unpacked")
    assert runtime.name == f"repoctl-{version}"
    for rel in ("AGENTS.md", "docs/BOARD.md", "docs/PRD.md",
                "docs/tasks/TEMPLATE.md", "docs/tasks/PARENT_TEMPLATE.md",
                "tests/repoctl/test_upgrade.py"):
        assert not (runtime / rel).exists(), rel
    for rel in ("tools/repoctl/cli.py", "tools/repoctl/graph_workflow.py",
                "tools/repoctl/knowledge_records.py", "fixtures/conformance/cases.json",
                "contracts/template-document-v1.json"):
        assert (runtime / rel).is_file(), rel
    assert not (runtime / "tests").exists()
    assert (runtime / "contracts/template-document-v1.json").read_bytes() == (
        PINNED_TEMPLATE / "docs/template-contract.json"
    ).read_bytes()


def test_pinned_template_conformance_in_external_workspace(tmp_path: Path) -> None:
    version = json.loads((ROOT / "repoctl-upgrade-manifest.json").read_text())["version"]
    runtime = _extract(build_release_archive(ROOT, tmp_path / "dist"), tmp_path / "unpacked")
    work = tmp_path / "template"
    shutil.copytree(PINNED_TEMPLATE, work)
    (work / "docs/BOARD.md").write_bytes((CONFORMANCE / "board.md").read_bytes())
    spec = work / "docs/specs/conformance.md"
    spec.parent.mkdir(parents=True)
    spec.write_bytes((CONFORMANCE / "context-doc.md").read_bytes())
    for case in json.loads((CONFORMANCE / "cases.json").read_text())["cases"]:
        task_path = work / "docs/tasks/T-20260609184046Z--conformance.md"
        task_path.write_bytes((CONFORMANCE / case["path"]).read_bytes())
        probe = _run(runtime, work, "task", "resume", "--json")
        payload = json.loads(probe.stdout)
        guidance = (payload.get("data") or {}).get("resume_guidance")
        assert not guidance or guidance["executable_handoff"] is None, case["id"]
        if case["expected"] == "unbound_handoff":
            assert probe.returncode == 0, payload
            assert guidance["status"] == "inactive"
            assert "handoff_unbound" in guidance["handoff"]["reason_codes"]
        else:
            assert probe.returncode != 0 or not guidance or guidance["status"] != "current", case["id"]

    task_path.unlink()
    (work / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n## Backlog\n", encoding="utf-8")
    valid = _run(runtime, work, "--version")
    assert valid.returncode == 0 and valid.stdout.strip() == version
    no_live = _run(runtime, work, "task", "resume", "--json")
    assert no_live.returncode == 0, no_live.stderr
    assert json.loads(no_live.stdout)["data"]["selection"]["status"] == "no_live"

    marker = work / "docs/template-contract.json"
    data = json.loads(marker.read_text())
    data["version"] = "2.0.0"
    marker.write_text(json.dumps(data), encoding="utf-8")
    bad = _run(runtime, work, "task", "resume", "--json")
    assert bad.returncode == 2
    assert json.loads(bad.stdout)["problems"][0]["code"] == "template_contract_unsupported"
    assert not (work / "docs/tasks/.repoctl-state").exists()
