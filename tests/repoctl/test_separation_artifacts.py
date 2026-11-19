"""Run the public archive against the independent document contract."""
from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "tests/fixtures/template-contract-2.0.0"
CONFORMANCE = TEMPLATE / "fixtures/conformance"
CASES = json.loads((CONFORMANCE / "cases.json").read_text())["cases"]


@pytest.fixture(scope="module")
def runtime(tmp_path_factory) -> Path:
    build_archive = runpy.run_path(
        str(ROOT / ".github/scripts/build_release_archive.py"),
    )["build_archive"]
    work = tmp_path_factory.mktemp("published-runtime")
    archive_path = build_archive(ROOT, work / "dist")
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(work / "unpacked", filter="data")
    return next((work / "unpacked").iterdir())


def run(runtime: Path, workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for name in ("PYTHON", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    env["REPOCTL_WORKSPACE_ROOT"] = str(workspace)
    return subprocess.run(
        [str(runtime / "scripts/repoctl"), *args],
        cwd=workspace, env=env, capture_output=True, text=True, timeout=20,
    )


def test_archive_runs_outside_the_source_checkout(runtime: Path, tmp_path: Path) -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    result = run(runtime, tmp_path, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == version
    result = run(runtime, tmp_path, "--help")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("contract_version", ["1.0.0", "2.0.0"])
def test_shared_task_fields_and_explicit_invalid_results(
    runtime: Path, tmp_path: Path, case: dict, contract_version: str,
) -> None:
    work = tmp_path / "workspace"
    shutil.copytree(TEMPLATE, work)
    (work / "docs/BOARD.md").unlink()
    marker = work / "docs/template-contract.json"
    contract = json.loads(marker.read_text())
    contract["version"] = contract_version
    marker.write_text(json.dumps(contract) + "\n")
    task = work / "docs/tasks/T-20260609184046Z--payment-retries.md"
    task.write_bytes((CONFORMANCE / case["path"]).read_bytes())
    before = {p.relative_to(work).as_posix(): p.read_bytes() for p in work.rglob("*") if p.is_file()}

    result = run(runtime, work, "task", "resume", "T-20260609184046Z", "--json")
    payload = json.loads(result.stdout)
    if case["expected"] == "valid":
        assert result.returncode == 0, payload
        visible = json.dumps(payload)
        assert "Compare the retry handler with the recorded provider event." in visible
        assert "A retried event reuses the original charge receipt." in visible
    else:
        assert result.returncode != 0, payload
        assert case["expected"] in {problem["code"] for problem in payload["problems"]}
    assert {p.relative_to(work).as_posix(): p.read_bytes() for p in work.rglob("*") if p.is_file()} == before
