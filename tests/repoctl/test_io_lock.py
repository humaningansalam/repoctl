from pathlib import Path
import os
import subprocess
import sys

import pytest

from tools.repoctl.io import RepoctlError, repoctl_lock


def test_separate_graph_lock_does_not_block_document_changes(tmp_path: Path):
    with repoctl_lock(tmp_path, resource="graph-main"):
        with repoctl_lock(tmp_path, timeout=0):
            pass


def test_os_lock_excludes_writers_and_releases_after_process_exit(tmp_path: Path):
    source_root = Path(__file__).resolve().parents[2]
    script = (
        "import os,sys; from pathlib import Path; from tools.repoctl.io import repoctl_lock; "
        "lock = repoctl_lock(Path(sys.argv[1])); lock.__enter__(); "
        "print('locked', flush=True); sys.stdin.readline(); os._exit(0)"
    )
    process = subprocess.Popen([sys.executable, "-B", "-c", script, str(tmp_path)],
                               cwd=source_root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(RepoctlError) as failure:
            with repoctl_lock(tmp_path, timeout=0):
                pytest.fail("a second writer acquired the same resource")
        assert failure.value.code == "workspace_busy"
        process.communicate(input="exit\n", timeout=10)
        assert process.returncode == 0
        with repoctl_lock(tmp_path, timeout=0):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
