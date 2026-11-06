from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.takeover_pilot import DEFAULT_CASES, bounded_retrieval, evaluate, main, run, source_bytes


def test_pilot_questions_do_not_leak_gold_paths() -> None:
    cases = json.loads(DEFAULT_CASES.read_text())["cases"]
    assert len(cases) >= 6
    assert {case["id"] for case in cases} >= {
        "natural-localization", "takeover", "opposite-area-distractor",
        "requirement-drift", "knowledge-reuse", "nonflask-localization",
    }
    for case in cases:
        assert case["gold_paths"], case["id"]
        assert all(path not in case["question"] for path in case["gold_paths"])
        assert all(path not in case["retrieval_pattern"] for path in case["gold_paths"])


def test_pilot_evaluation_requires_actual_path_recall() -> None:
    expected = ["src/worker/job_runner.py", "tests/test_job_runner.py"]
    assert evaluate("no source evidence", expected)["recall_count"] == 0
    assert evaluate("tests/test_job_runner.py", expected)["recall_count"] == 1
    assert evaluate("src/worker/job_runner.py tests/test_job_runner.py", expected)["recall_count"] == 2


def test_pilot_runner_reports_bytes_and_call_failure_separately(tmp_path: Path) -> None:
    completed = run(["git", "--version"], tmp_path, 5)
    assert completed["exit_code"] == 0
    assert completed["latency_seconds"] >= 0
    assert "git version" in completed["stdout"]
    failed = run(["nonexistent-pilot-command-449f3"], tmp_path, 5)
    assert failed["exit_code"] is None
    assert "error" in failed


def test_local_harness_rejects_model_runner_option(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", [
        "takeover_pilot", "--repo", str(tmp_path),
        "--adopter", "basic-ml-api",
        "--condition", "repository-only", "--out", str(tmp_path / "out.json"),
        "--model", "any/model",
    ])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_unknown_adopter_does_not_silently_pass_empty_cases(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(sys, "argv", [
        "takeover_pilot", "--repo", str(tmp_path),
        "--adopter", "no-such-fixture-adopter",
        "--condition", "repository-only", "--out", str(tmp_path / "out.json"),
    ])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not (tmp_path / "out.json").exists()


def test_source_bytes_streams_full_large_git_index_without_tail_bias(tmp_path: Path) -> None:
    repo = tmp_path / "large"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    expected_bytes = 0
    for index in range(4000):
        source = repo / f"source_{index:04d}_context_module.py"
        data = f"answer_{index} = {index}\n"
        source.write_text(data, encoding="utf-8")
        expected_bytes += len(data.encode("utf-8"))
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    index_bytes = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo)
    assert len(index_bytes) > 50_000

    measured = source_bytes(repo)
    assert measured["files_considered"] == 4000
    assert measured["files_measured"] == 4000
    assert measured["bytes_measured"] == expected_bytes
    assert measured["truncated"] is False
    assert measured["truncation_reason"] is None
    bounded = source_bytes(repo, file_budget=60)
    assert bounded["files_measured"] == 60
    assert bounded["truncated"] is True
    assert bounded["truncation_reason"] == "source_file_budget"


def test_global_retrieval_preserves_early_middle_late_gold_paths(tmp_path: Path) -> None:
    repo = tmp_path / "large"
    repo.mkdir()
    paths: list[str] = []
    for index in range(5000):
        name = f"source_{index:04d}_" + "distractor_" * 8 + ".py"
        source = repo / name
        source.write_text("shared_search_token\n", encoding="utf-8")
        if index in (0, 2500, 4999):
            paths.append(name)
    measurement = bounded_retrieval(repo, "shared_search_token")
    assert measurement["exit_code"] == 0
    assert measurement["retrieval_output_bytes"] > 50_000
    assert measurement["truncated"] is False
    assert measurement["retrieval_complete"] is True
    recall = evaluate(measurement["stdout"], paths)
    assert recall["recall_count"] == recall["gold_count"] == 3

    limited = bounded_retrieval(repo, "shared_search_token", output_budget=4096)
    assert limited["truncated"] is True
    assert limited["truncation_reason"] == "retrieval_output_budget"
    assert limited["retrieval_complete"] is False
    assert limited["retrieval_output_bytes"] <= 4096
    count_limited = bounded_retrieval(repo, "shared_search_token", result_budget=3)
    assert count_limited["truncated"] is True
    assert count_limited["truncation_reason"] == "retrieval_result_budget"
    assert count_limited["retrieval_paths_returned"] == 3
