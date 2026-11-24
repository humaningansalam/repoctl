from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .io import RepoctlError, atomic_write


REPO_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
PRODUCT_REPO_DIRS = ("repos",)


class RepositoryIdentitySource(StrEnum):
    WORKSPACE = "workspace"
    RESERVED = "reserved"
    PINNED = "pinned"


@dataclass(frozen=True)
class RepoTarget:
    id: str
    root_path: Path
    display_path: str
    identity_source: RepositoryIdentitySource

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity_source", RepositoryIdentitySource(self.identity_source))

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "path": self.display_path,
            "identity_source": self.identity_source.value,
        }


class RepoSelectorStatus(StrEnum):
    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class RepoSelectorResolution:
    status: RepoSelectorStatus
    path: str = ""
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoCandidate:
    display_path: str
    suggested_id: str
    git_toplevel: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.display_path,
            "suggested_id": self.suggested_id,
            "git_toplevel": self.git_toplevel,
        }


@dataclass(frozen=True)
class RepoLayout:
    placement: str
    targets: tuple[RepoTarget, ...]
    candidates: tuple[RepoCandidate, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "placement": self.placement,
            "targets": [target.to_dict() for target in self.targets],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def _safe_rel(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    if raw in {".", "./"}:
        return "."
    if Path(raw).is_absolute():
        raise RepoctlError(
            f"repository path must be workspace-relative: {value}",
            code="repository_topology_invalid",
            path=value,
        )
    while raw.startswith("./"):
        raw = raw[2:]
    raw = raw.strip("/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise RepoctlError(f"invalid repository path: {value}", code="repository_topology_invalid", path=value)
    return "/".join(parts)


def resolve_repo_selector_path(
    value: str,
    *,
    repository_path: str = "",
    known_paths: set[str] | None = None,
) -> RepoSelectorResolution:
    """Resolve repo-relative and selected workspace-relative selectors without guessing."""
    raw = value.strip()
    if not raw or "\\" in raw or Path(raw).is_absolute():
        return RepoSelectorResolution(RepoSelectorStatus.INVALID)
    while raw.startswith("./"):
        raw = raw[2:]
    raw = raw.strip("/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return RepoSelectorResolution(RepoSelectorStatus.INVALID)
    normalized = "/".join(parts)
    prefix = repository_path.strip().replace("\\", "/").strip("/")
    workspace_candidate = ""
    if prefix and normalized.startswith(prefix + "/"):
        workspace_candidate = normalized[len(prefix) + 1 :]
    candidates = tuple(dict.fromkeys(path for path in (normalized, workspace_candidate) if path))
    if known_paths is None:
        return RepoSelectorResolution(RepoSelectorStatus.RESOLVED, workspace_candidate or normalized)
    existing = tuple(path for path in candidates if path in known_paths)
    if len(existing) > 1:
        return RepoSelectorResolution(RepoSelectorStatus.AMBIGUOUS, candidates=existing)
    if existing:
        return RepoSelectorResolution(RepoSelectorStatus.RESOLVED, existing[0])
    return RepoSelectorResolution(RepoSelectorStatus.NOT_FOUND, workspace_candidate or normalized)


def _git_toplevel(path: Path) -> Path | None:
    if not path.exists():
        return None
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        return None
    try:
        return Path(result.stdout.strip()).resolve()
    except OSError:
        return None


def is_git_repo_root(path: Path) -> bool:
    git_marker = path / ".git"
    if not git_marker.exists():
        return False
    top = _git_toplevel(path)
    try:
        return top == path.resolve()
    except OSError:
        return False


def _target(root: Path, repo_id: str, rel: str, *, identity_source: str, require_git: bool = True) -> RepoTarget:
    if not REPO_ID_RE.match(repo_id):
        raise RepoctlError(f"invalid repository id: {repo_id}")
    rel = _safe_rel(rel)
    path = root / rel
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)
    except (OSError, ValueError):
        raise RepoctlError(f"repository path must stay inside workspace: {rel}")
    if require_git and not is_git_repo_root(path):
        raise RepoctlError(f"repository path is not a git top-level: {rel}")
    return RepoTarget(repo_id, path, rel, identity_source)


def _configured_targets(root: Path, settings: dict[str, Any]) -> tuple[RepoTarget, ...] | None:
    raw = settings.get("repositories")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise RepoctlError("docs/repoctl.json repositories must be an array")
    targets: list[RepoTarget] = []
    ids: set[str] = set()
    paths: set[str] = set()
    resolved_roots: set[str] = set()
    git_toplevels: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise RepoctlError("docs/repoctl.json repositories entries must be objects")
        repo_id = item.get("id")
        path = item.get("path")
        if not isinstance(repo_id, str) or not repo_id.strip():
            raise RepoctlError("repository id must be a non-empty string")
        if not isinstance(path, str) or not path.strip():
            raise RepoctlError("repository path must be a non-empty string")
        folded = repo_id.strip().casefold()
        if folded in ids:
            raise RepoctlError(f"duplicate repository id: {repo_id}")
        ids.add(folded)
        rel = _safe_rel(path)
        if rel in paths:
            raise RepoctlError(f"duplicate repository path: {rel}")
        paths.add(rel)
        target = _target(root, repo_id.strip(), rel, identity_source="pinned", require_git=False)
        resolved_root = target.root_path.resolve().as_posix()
        if resolved_root in resolved_roots:
            raise RepoctlError(f"duplicate repository real path: {rel}", code="repository_topology_invalid", path=rel)
        resolved_roots.add(resolved_root)
        git_top = _git_toplevel(target.root_path)
        git_top_key = git_top.as_posix() if git_top else resolved_root
        if git_top_key in git_toplevels:
            raise RepoctlError(f"duplicate repository git root: {rel}", code="repository_topology_invalid", path=rel)
        git_toplevels.add(git_top_key)
        targets.append(target)
    return tuple(targets)


def _discover_git_roots(root: Path) -> list[str]:
    candidates: list[str] = []
    for rel in PRODUCT_REPO_DIRS:
        path = root / rel
        if is_git_repo_root(path):
            candidates.append(rel)
    repos_dir = root / "repos"
    if repos_dir.is_dir():
        for child in sorted(repos_dir.iterdir()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            rel = f"repos/{child.name}"
            if is_git_repo_root(child):
                candidates.append(rel)
    return candidates


def _suggested_id_for_candidate(rel: str) -> str:
    return rel.rsplit("/", 1)[-1]


def _candidates_for_discovered(root: Path, discovered: list[str]) -> tuple[RepoCandidate, ...]:
    candidates: list[RepoCandidate] = []
    for rel in discovered:
        path = root / rel
        top = _git_toplevel(path)
        candidates.append(RepoCandidate(rel, _suggested_id_for_candidate(rel), top.as_posix() if top else ""))
    return tuple(candidates)


def _layout_from_settings(root: Path, settings: dict[str, Any]) -> RepoLayout:
    configured = _configured_targets(root, settings)
    discovered = _discover_git_roots(root)
    if configured is not None:
        extras = [path for path in discovered if path not in {target.display_path for target in configured}]
        layout = RepoLayout("collection", configured, candidates=_candidates_for_discovered(root, extras))
    elif "repos" in discovered:
        layout = RepoLayout("direct", (_target(root, "main", "repos", identity_source="reserved"),))
    elif not discovered and is_git_repo_root(root):
        layout = RepoLayout("direct", (_target(root, "main", ".", identity_source="reserved"),))
    else:
        candidates = _candidates_for_discovered(root, discovered)
        layout = RepoLayout("collection" if candidates else "empty", (), candidates=candidates)
    return layout


def repo_layout(root: Path) -> RepoLayout:
    return _layout_from_settings(root, _load_settings(root))


def default_repo_target(root: Path, *, layout: RepoLayout | None = None) -> RepoTarget | None:
    layout = layout or repo_layout(root)
    if not layout.targets:
        return None
    if len(layout.targets) > 1:
        raise RepoctlError("multiple product repositories configured; pass --repo-id", code="repository_selector_required")
    return _require_git_target(root, layout.targets[0])


def _require_git_target(root: Path, target: RepoTarget) -> RepoTarget:
    from .git import repo_git_state

    state = repo_git_state(root, target)
    if not state.available:
        raise RepoctlError(state.reason, code="repository_git_unavailable", path=state.repo_path or target.display_path)
    return target


def workspace_target(root: Path) -> RepoTarget:
    """Select documents at the workspace root."""
    return RepoTarget("", root, ".", RepositoryIdentitySource.WORKSPACE)


def source_target(root: Path, *, repo_id: str | None = None, workspace: bool = False) -> RepoTarget:
    return workspace_target(root) if workspace else require_repo_target(root, repo_id=repo_id)


def require_repo_target(root: Path, repo_id: str | None = None, *, layout: RepoLayout | None = None) -> RepoTarget:
    layout = layout or repo_layout(root)
    if not layout.targets:
        raise RepoctlError("product repository is missing", code="repository_not_found")
    if repo_id:
        for target in layout.targets:
            if target.id == repo_id:
                return _require_git_target(root, target)
        raise RepoctlError(f"repository not found: {repo_id}", code="repository_not_found")
    if len(layout.targets) > 1:
        raise RepoctlError("multiple product repositories configured; pass --repo-id", code="repository_selector_required")
    return _require_git_target(root, layout.targets[0])


def repo_check_problems(root: Path, layout: RepoLayout) -> list[dict[str, str]]:
    from .git import repo_git_state

    problems: list[dict[str, str]] = []
    for target in layout.targets:
        state = repo_git_state(root, target)
        if not state.available:
            problems.append(
                {
                    "severity": "error",
                    "code": "repository_git_unavailable",
                    "message": state.reason,
                    "repo_id": state.repo_id or target.id,
                    "path": state.repo_path or target.display_path,
                    "reason": state.reason,
                }
            )
    if layout.candidates:
        for candidate in layout.candidates:
            problems.append(
                {
                    "severity": "warning",
                    "code": "repository_identity_unbound",
                    "message": f"repository candidate has no pinned stable id: {candidate.display_path}",
                    "path": candidate.display_path,
                }
            )
    return problems


def adopt_repositories(root: Path, *, all_candidates: bool = False, path: str = "", repo_id: str = "") -> RepoLayout:
    layout = repo_layout(root)
    if all_candidates and repo_id:
        raise RepoctlError("--id selects one repository; omit it with --all")
    if all_candidates:
        selected = [(candidate.suggested_id, candidate.display_path) for candidate in layout.candidates]
    else:
        rel = _safe_rel(path)
        selected = [(repo_id or ("main" if rel == "repos" else _suggested_id_for_candidate(rel)), rel)]
    if not selected:
        raise RepoctlError("no repository candidates to adopt")
    settings = _load_settings(root)
    entries = [{"id": target.id, "path": target.display_path} for target in layout.targets]
    for identity, rel in selected:
        _target(root, identity, rel, identity_source="pinned")
        entries.append({"id": identity, "path": rel})
    new_settings = {**settings, "repositories": entries}
    validated = _layout_from_settings(root, new_settings)
    atomic_write(root / "docs/repoctl.json", json.dumps(new_settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return validated


def _load_settings(root: Path) -> dict[str, Any]:
    path = root / "docs/repoctl.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RepoctlError(f"invalid docs/repoctl.json: {exc.msg}", code="invalid_repoctl_settings", path="docs/repoctl.json") from exc
    if not isinstance(data, dict):
        raise RepoctlError("docs/repoctl.json must contain a JSON object", code="invalid_repoctl_settings", path="docs/repoctl.json")
    return data
