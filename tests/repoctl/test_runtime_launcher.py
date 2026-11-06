from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest


def _fake_runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "release"
    (runtime / "scripts").mkdir(parents=True)
    (runtime / "tools/repoctl").mkdir(parents=True)
    source_root = Path(__file__).resolve().parents[2]
    shutil.copy2(source_root / "scripts/repoctl", runtime / "scripts/repoctl")
    (runtime / "tools/__init__.py").write_text("", encoding="utf-8")
    (runtime / "tools/repoctl/__init__.py").write_text("", encoding="utf-8")
    (runtime / "tools/repoctl/__main__.py").write_text(
        "import json, sys\nprint(json.dumps({'executable': sys.executable}))\n",
        encoding="utf-8",
    )
    venv.EnvBuilder(with_pip=False).create(runtime / ".venv")
    return runtime


def _invoke(runtime: Path, *, python_override: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("PYTHON", None)
    env.pop("PYTHONPATH", None)
    env["VIRTUAL_ENV"] = "/not-the-runtime/product/.venv"
    if python_override is not None:
        env["PYTHON"] = python_override
    return subprocess.run(
        [str(runtime / "scripts/repoctl")],
        cwd=runtime.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def test_normal_launcher_uses_own_venv_not_product_environment(tmp_path: Path) -> None:
    runtime = _fake_runtime(tmp_path)
    result = _invoke(runtime)
    assert result.returncode == 0, result.stderr
    interpreter = Path(json.loads(result.stdout)["executable"])
    assert interpreter.parent == runtime / ".venv/bin"


def test_explicit_python_override_is_honored_and_invalid_choice_fails(tmp_path: Path) -> None:
    runtime = _fake_runtime(tmp_path)
    selected = _invoke(runtime, python_override=sys.executable)
    assert selected.returncode == 0, selected.stderr
    assert json.loads(selected.stdout)["executable"] == sys.executable

    rejected = _invoke(runtime, python_override="/deliberately/missing/python")
    assert rejected.returncode == 127
    assert "interpreter is unavailable" in rejected.stderr


def test_broken_runtime_venv_does_not_silently_use_system_python(tmp_path: Path) -> None:
    runtime = _fake_runtime(tmp_path)
    (runtime / ".venv/pyvenv.cfg").unlink()
    rejected = _invoke(runtime)
    assert rejected.returncode == 127
    assert "has no pyvenv.cfg" in rejected.stderr
