from __future__ import annotations

import hashlib
import json
import runpy
import shutil
import tarfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
build_archive = runpy.run_path(str(ROOT / ".github/scripts/build_release_archive.py"))["build_archive"]


def test_published_archive_has_verified_contents_and_reproducible_bytes(tmp_path: Path) -> None:
    first = build_archive(ROOT, tmp_path / "first")
    second = build_archive(ROOT, tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    prefix = f"repoctl-{version}/"
    with tarfile.open(first, "r:gz") as archive:
        members = archive.getmembers()
        assert all(member.isfile() and member.name.startswith(prefix) for member in members)
        payload = {member.name.removeprefix(prefix): archive.extractfile(member).read() for member in members}
        assert archive.getmember(prefix + "scripts/repoctl").mode == 0o755

    manifest = json.loads(payload.pop("ARTIFACT-MANIFEST.json"))
    assert manifest["package"] == "repoctl"
    assert manifest["version"] == version
    assert {entry["path"] for entry in manifest["files"]} == set(payload)
    for entry in manifest["files"]:
        content = payload[entry["path"]]
        assert len(content) == entry["size"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
    assert payload["LICENSE"] == (ROOT / "LICENSE").read_bytes()
    assert "scripts/repoctl" in payload and "tools/repoctl/__main__.py" in payload
    assert not any(name.startswith(("tests/", "fixtures/", ".github/", "docs/tasks/")) for name in payload)
    assert "AGENTS.md" not in payload and "docs/BOARD.md" not in payload
    if "tools/repoctl/providers/typescript-5.9.3.js.gz" in payload:
        assert payload["tools/repoctl/providers/typescript-LICENSE.txt"] == (
            ROOT / "tools/repoctl/providers/typescript-LICENSE.txt"
        ).read_bytes()
    checksum, name = (first.parent / "SHA256SUMS").read_text().split()
    assert name == first.name
    assert checksum == hashlib.sha256(first.read_bytes()).hexdigest()


@pytest.mark.parametrize("failure", ["missing", "escaping_symlink"])
def test_bad_source_keeps_the_previous_archive(tmp_path: Path, failure: str) -> None:
    source = tmp_path / "source"
    for relative in ("LICENSE", "README.md", "pyproject.toml", "uv.lock",
                     "scripts/repoctl", "tools/__init__.py"):
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    archive = build_archive(source, tmp_path / "output")
    previous = archive.read_bytes()
    (source / "LICENSE").unlink()
    if failure == "escaping_symlink":
        outside = tmp_path / "outside-license"
        outside.write_text("outside")
        (source / "LICENSE").symlink_to(outside)

    with pytest.raises((FileNotFoundError, ValueError)):
        build_archive(source, archive.parent)
    assert archive.read_bytes() == previous
