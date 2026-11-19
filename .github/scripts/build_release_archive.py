#!/usr/bin/env python3
"""Build the current source tree's release archive and checksums."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "repoctl"


def package_version(root: Path) -> str:
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.+-]*", version):
        raise ValueError("invalid release version")
    return version


def source_paths(root: Path) -> list[Path]:
    paths = [Path(name) for name in ("LICENSE", "README.md", "pyproject.toml", "uv.lock", "scripts/repoctl", "tools/__init__.py")]
    for path in (root / "tools/repoctl").rglob("*"):
        if path.is_file() and (path.suffix in {".py", ".js", ".dart", ".csx", ".gz"} or path.name.endswith("-LICENSE.txt")):
            paths.append(path.relative_to(root))
    paths.extend(path.relative_to(root) for path in (root / "docs/contracts").glob("*.md"))
    return sorted(set(paths))


def build_archive(root: Path, output_dir: Path) -> Path:
    version = package_version(root)
    contents: dict[str, bytes] = {}
    for relative in source_paths(root):
        source = root / relative
        source.resolve(strict=True).relative_to(root.resolve())
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"release input must be a regular file: {relative}")
        contents[relative.as_posix()] = source.read_bytes()
    manifest = {
        "schema": f"{PACKAGE}.release-artifact",
        "schema_version": 1,
        "package": PACKAGE,
        "version": version,
        "files": [
            {"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            for name, raw in sorted(contents.items())
        ],
    }
    contents["ARTIFACT-MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    prefix = f"{PACKAGE}-{version}"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{prefix}.tar.gz"
    with tempfile.NamedTemporaryFile(dir=output_dir, prefix=f".{prefix}.", suffix=".tmp", delete=False) as output:
        temporary = Path(output.name)
        try:
            with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped:
                with tarfile.open(fileobj=zipped, mode="w") as archive:
                    for name, raw in sorted(contents.items()):
                        info = tarfile.TarInfo(f"{prefix}/{name}")
                        info.size = len(raw)
                        info.mode = 0o755 if name == "scripts/repoctl" else 0o644
                        info.uid = info.gid = info.mtime = 0
                        info.uname = info.gname = ""
                        archive.addfile(info, io.BytesIO(raw))
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    (output_dir / "SHA256SUMS").write_text(f"{checksum}  {destination.name}\n", encoding="ascii")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory", type=Path, nargs="?", default=ROOT / "dist")
    args = parser.parse_args()
    print(build_archive(ROOT, args.output_directory))


if __name__ == "__main__":
    main()
