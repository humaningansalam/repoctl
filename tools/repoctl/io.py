from __future__ import annotations

import errno
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class RepoctlError(RuntimeError):
    """Base error for repoctl user-facing failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "repoctl_error",
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


@dataclass(frozen=True)
class Problem:
    severity: str
    code: str
    message: str
    path: str | None = None
    cause_code: str | None = None

    def to_dict(self) -> dict[str, str]:
        data = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.path is not None:
            data["path"] = self.path
        if self.cause_code is not None:
            data["cause_code"] = self.cause_code
        return data


def find_workspace_root(explicit: str | Path | None = None) -> Path:
    """Resolve an explicit directory or discover the workspace above cwd."""
    external = explicit if explicit is not None else os.environ.get("REPOCTL_WORKSPACE_ROOT")
    if external is not None:
        try:
            candidate = Path(external).expanduser().resolve(strict=True)
        except (OSError, ValueError, RuntimeError) as error:
            raise RepoctlError("workspace root is inaccessible", code="workspace_root_invalid") from error
        if not candidate.is_dir():
            raise RepoctlError("workspace root is not a directory", code="workspace_root_invalid", path=str(candidate))
        return candidate
    here = Path.cwd().resolve()
    ancestors = (here, *here.parents)
    for candidate in ancestors:
        if ((candidate / "docs/tasks").is_dir() or (candidate / "docs/repoctl.json").is_file()
                or (candidate / ".git").exists()):
            return candidate
    raise RepoctlError("workspace not found; pass --workspace-root or set REPOCTL_WORKSPACE_ROOT", code="workspace_root_not_found")


@contextmanager
def repoctl_lock(root: Path, *, resource: str = "documents", timeout: float = 30.0, interval: float = 0.05) -> Iterator[None]:
    """Serialize writers to one resource; process exit releases the OS lock."""
    if not resource or resource in {".", ".."} or "/" in resource or "\\" in resource:
        raise ValueError("lock resource must be one filename component")
    path = root / ".repoctl-state/locks" / f"{resource}.lock"
    _reject_symlinked_parent_chain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import msvcrt

        def acquire(handle) -> None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

        def release(handle) -> None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        def acquire(handle) -> None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        def release(handle) -> None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    with path.open("a+b", buffering=0) as handle:
        if os.name == "nt" and path.stat().st_size == 0:
            handle.write(b"\0")
        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire(handle)
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise RepoctlError(f"another repoctl writer holds {resource}", code="workspace_busy", path=path.relative_to(root).as_posix()) from error
                time.sleep(interval)
        try:
            yield
        finally:
            release(handle)


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    _reject_symlinked_parent_chain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = write_temporary_text(path, text)
    try:
        os.replace(tmp, path)
    except Exception:
        if tmp.exists() and not tmp.is_symlink():
            tmp.unlink()
        raise


def _reject_symlinked_parent_chain(path: Path) -> None:
    """Never let a state write cross an existing symlinked path component."""

    current = path.parent
    while current != current.parent:
        if current.is_symlink():
            raise RepoctlError(
                "write path crosses a symlinked parent",
                code="unsafe_write_path",
                path=path.as_posix(),
            )
        current = current.parent


def write_temporary_text(path: Path, text: str, *, suffix: str = ".tmp") -> Path:
    """Create one durable, collision-free sibling file for an atomic publish step."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}{suffix}")
        try:
            handle = temporary.open("x", encoding="utf-8", newline="")
        except FileExistsError:
            continue
        break
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        raise
    return temporary
