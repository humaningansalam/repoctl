from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.repoctl.io import RepoctlError
from tools.repoctl.inventory import file_inventory
from tools.repoctl.repositories import (
    adopt_repositories,
    repo_layout,
    require_repo_target,
    resolve_repo_selector_path,
    RepoSelectorStatus,
)


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=repo, check=True)


def commit_all(repo: Path) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)


def write_settings(root: Path, data: dict) -> None:
    path = root / "docs/repoctl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def test_single_direct_repository_and_git_worktree(tmp_path: Path) -> None:
    source = tmp_path / "source"
    init_repo(source)
    (source / "app.py").write_text("answer = 42\n")
    commit_all(source)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(tmp_path / "repos"), "HEAD"],
        cwd=source,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    selected = require_repo_target(tmp_path)
    assert (selected.id, selected.display_path) == ("main", "repos")
    assert (selected.root_path / ".git").is_file()


def test_explicit_paths_do_not_require_a_repos_directory(tmp_path: Path) -> None:
    init_repo(tmp_path / "git/product")
    selected = adopt_repositories(
        tmp_path, path="git/product", repo_id="product"
    ).targets[0]
    assert selected.display_path == "git/product"
    assert require_repo_target(tmp_path, repo_id="product") == selected


def test_registered_repo_remains_usable_when_an_unregistered_repo_exists(
    tmp_path: Path,
) -> None:
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    adopt_repositories(tmp_path, path="repos/web", repo_id="web")
    assert require_repo_target(tmp_path, repo_id="web").display_path == "repos/web"
    assert [item.display_path for item in repo_layout(tmp_path).candidates] == [
        "repos/api"
    ]


def test_multiple_registered_repositories_require_an_explicit_selector(
    tmp_path: Path,
) -> None:
    for name in ("web", "api"):
        init_repo(tmp_path / "repos" / name)
    adopt_repositories(tmp_path, all_candidates=True)
    with pytest.raises(RepoctlError, match="repo-id"):
        require_repo_target(tmp_path)
    assert require_repo_target(tmp_path, repo_id="api").display_path == "repos/api"


@pytest.mark.parametrize("relative", ["../elsewhere", "/elsewhere"])
def test_repository_paths_cannot_escape_workspace(
    tmp_path: Path, relative: str
) -> None:
    write_settings(tmp_path, {"repositories": [{"id": "app", "path": relative}]})
    with pytest.raises(RepoctlError):
        repo_layout(tmp_path)


def test_repository_alias_cannot_duplicate_one_real_root(tmp_path: Path) -> None:
    init_repo(tmp_path / "app")
    (tmp_path / "alias").symlink_to(tmp_path / "app", target_is_directory=True)
    write_settings(
        tmp_path,
        {
            "repositories": [
                {"id": "app", "path": "app"},
                {"id": "alias", "path": "alias"},
            ]
        },
    )
    with pytest.raises(RepoctlError, match="duplicate"):
        repo_layout(tmp_path)


def test_explicit_nested_git_repository_is_valid(tmp_path: Path) -> None:
    init_repo(tmp_path / "app")
    init_repo(tmp_path / "app/module")
    write_settings(
        tmp_path,
        {
            "repositories": [
                {"id": "app", "path": "app"},
                {"id": "module", "path": "app/module"},
            ]
        },
    )
    assert (
        require_repo_target(tmp_path, repo_id="module").root_path
        == tmp_path / "app/module"
    )


def test_selector_reports_ambiguous_repository_and_workspace_paths() -> None:
    result = resolve_repo_selector_path(
        "repos/app.py", repository_path="repos", known_paths={"repos/app.py", "app.py"}
    )
    assert result.status == RepoSelectorStatus.AMBIGUOUS
    assert set(result.candidates) == {"repos/app.py", "app.py"}


def test_inventory_uses_git_ignore_and_preserves_unusual_filenames(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / ".gitignore").write_text("ignored.py\n")
    (repo / "ignored.py").write_text("secret = 1\n")
    name = " leading\n한글.py"
    (repo / name).write_text("value = 1\n")
    (repo / "linked.py").symlink_to(tmp_path / "outside.py")
    files, problems, _ = file_inventory(tmp_path)
    assert not problems
    by_path = {item.path: item for item in files}
    assert name in by_path
    assert "ignored.py" not in by_path
    assert by_path["linked.py"].classification == "excluded"
    assert not (repo / ".repometa").exists()
