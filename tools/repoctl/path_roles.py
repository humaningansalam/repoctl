from __future__ import annotations

from pathlib import PurePosixPath


TEST_DIRECTORIES = {"test", "tests", "__tests__"}
TEST_SUFFIXES = (
    "_test.py",
    "_test.go",
    ".test.js",
    ".test.jsx",
    ".test.mjs",
    ".test.mts",
    ".test.ts",
    ".test.tsx",
    "_test.mjs",
    "_test.mts",
    "_test.dart",
)


def is_test_path(path: str, *, repository_path: str = "") -> bool:
    normalized = path.replace("\\", "/").strip("/").casefold()
    parts = PurePosixPath(normalized).parts
    repository_parts = PurePosixPath(repository_path.replace("\\", "/").strip("/").casefold()).parts
    if repository_parts and parts[:len(repository_parts)] == repository_parts:
        parts = parts[len(repository_parts):]
    name = parts[-1] if parts else ""
    return any(part in TEST_DIRECTORIES for part in parts[:-1]) or name.startswith("test_") or name.endswith(TEST_SUFFIXES)
