#!/usr/bin/env python3
"""Run reproducible, local-only takeover retrieval baselines on disposable copies.

Prompts contain no gold paths or symbols. No network/model runner is invoked;
this baseline does not claim agent edit success or token measurements.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "tests/fixtures/takeover-drift-benchmark/pilot-cases.json"


SOURCE_SUFFIXES = (".py", ".go", ".js", ".ts", ".tsx")
SOURCE_FILE_BUDGET = 50_000
SOURCE_BYTES_BUDGET = 1_000_000_000
SOURCE_TIME_BUDGET_SECONDS = 20
RETRIEVAL_OUTPUT_BUDGET = 2_000_000
RETRIEVAL_RESULT_BUDGET = 100_000


def run(command: list[str], cwd: Path, timeout: int = 120) -> dict:
    """Small command wrapper; truncation is explicit and never tail-biased."""
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
        stdout_limit, stderr_limit = 50_000, 20_000
        return {
            "exit_code": completed.returncode,
            "stdout": completed.stdout[:stdout_limit],
            "stderr": completed.stderr[:stderr_limit],
            "truncated": len(completed.stdout) > stdout_limit or len(completed.stderr) > stderr_limit,
            "truncation_reason": "command_output_budget" if (
                len(completed.stdout) > stdout_limit or len(completed.stderr) > stderr_limit
            ) else None,
            "latency_seconds": round(time.monotonic() - started, 3),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"exit_code": None, "error": str(exc), "latency_seconds": round(time.monotonic() - started, 3),
                "truncated": True, "truncation_reason": "command_failed_or_timed_out"}


def source_bytes(
    root: Path, *, file_budget: int = SOURCE_FILE_BUDGET,
    byte_budget: int = SOURCE_BYTES_BUDGET, time_budget: int = SOURCE_TIME_BUDGET_SECONDS,
) -> dict:
    """Stream tracked names and stat source sizes; never truncate a path list silently."""
    result = {"files_considered": 0, "files_measured": 0, "bytes_measured": 0,
              "truncated": False, "truncation_reason": None, "exit_code": None}
    deadline = time.monotonic() + time_budget
    try:
        process = subprocess.Popen(["git", "ls-files", "-z", "--"], cwd=root,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        result.update(truncated=True, truncation_reason="git_ls_files_unavailable", error=str(exc))
        return result
    pending = bytearray()
    try:
        assert process.stdout is not None
        while True:
            if time.monotonic() > deadline:
                result.update(truncated=True, truncation_reason="source_time_budget")
                break
            chunk = process.stdout.read(16_384)
            if not chunk:
                break
            pending.extend(chunk)
            if len(pending) > 262_144 and b"\0" not in pending:
                result.update(truncated=True, truncation_reason="source_filename_budget")
                break
            while True:
                end = pending.find(0)
                if end < 0:
                    break
                raw_name = bytes(pending[:end])
                del pending[:end + 1]
                if result["files_considered"] >= file_budget:
                    result.update(truncated=True, truncation_reason="source_file_budget")
                    break
                result["files_considered"] += 1
                name = os.fsdecode(raw_name)
                rel = Path(name)
                if rel.is_absolute() or ".." in rel.parts or not name.endswith(SOURCE_SUFFIXES):
                    continue
                source = root / rel
                if source.is_symlink() or not source.is_file():
                    continue
                try:
                    size = source.stat().st_size
                except OSError:
                    result.update(truncated=True, truncation_reason="source_stat_error")
                    break
                if result["bytes_measured"] + size > byte_budget:
                    result.update(truncated=True, truncation_reason="source_byte_budget")
                    break
                result["files_measured"] += 1
                result["bytes_measured"] += size
            if result["truncated"]:
                break
        if pending and not result["truncated"]:
            result.update(truncated=True, truncation_reason="incomplete_git_filename")
    finally:
        if result["truncated"] and process.poll() is None:
            process.terminate()
        try:
            result["exit_code"] = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            result["exit_code"] = process.wait()
            result.update(truncated=True, truncation_reason="git_ls_files_timeout")
        if result["exit_code"] not in (0, None) and not result["truncated"]:
            result.update(truncated=True, truncation_reason="git_ls_files_failed")
    return result


def bounded_retrieval(
    root: Path, pattern: str, *, output_budget: int = RETRIEVAL_OUTPUT_BUDGET,
    result_budget: int = RETRIEVAL_RESULT_BUDGET, timeout: int = 120,
) -> dict:
    """Return global-budgeted matching *paths*, not a biased tail of rg output."""
    started = time.monotonic()
    command = ["rg", "-l", "--color", "never", "--", pattern, "."]
    result = {"exit_code": None, "stdout": "", "stderr": "",
              "retrieval_paths_returned": 0, "retrieval_output_bytes": 0,
              "truncated": False, "truncation_reason": None}
    try:
        process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
    except OSError as exc:
        return {**result, "error": str(exc), "truncated": True,
                "truncation_reason": "retrieval_unavailable",
                "latency_seconds": round(time.monotonic() - started, 3)}
    parts: list[bytes] = []
    try:
        assert process.stdout is not None
        while True:
            if time.monotonic() - started > timeout:
                result.update(truncated=True, truncation_reason="retrieval_time_budget")
                break
            # readline's size bound also bounds memory for a pathological filename.
            chunk = process.stdout.readline(8192)
            if not chunk:
                break
            if result["retrieval_output_bytes"] + len(chunk) > output_budget:
                result.update(truncated=True, truncation_reason="retrieval_output_budget")
                break
            if result["retrieval_paths_returned"] + chunk.count(b"\n") > result_budget:
                result.update(truncated=True, truncation_reason="retrieval_result_budget")
                break
            parts.append(chunk)
            result["retrieval_output_bytes"] += len(chunk)
            result["retrieval_paths_returned"] += chunk.count(b"\n")
    finally:
        if result["truncated"] and process.poll() is None:
            process.terminate()
        try:
            result["exit_code"] = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            result["exit_code"] = process.wait()
            result.update(truncated=True, truncation_reason="retrieval_time_budget")
        if result["exit_code"] not in (0, 1, None) and not result["truncated"]:
            result.update(truncated=True, truncation_reason="retrieval_failed")
    result["stdout"] = b"".join(parts).decode("utf-8", errors="replace")
    result["latency_seconds"] = round(time.monotonic() - started, 3)
    result["retrieval_complete"] = not result["truncated"]
    return result


def evaluate(text: str, expected: list[str]) -> dict:
    return {"gold_paths_recalled": [item for item in expected if item in text],
            "recall_count": sum(item in text for item in expected),
            "gold_count": len(expected)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--repo", type=Path, required=True, help="Disposable repo copy only")
    parser.add_argument("--adopter", required=True, help="Fixture adopter id; do not infer it from path")
    parser.add_argument("--condition", choices=("repository-only", "task-handoff"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = args.repo.resolve(strict=True)
    if not str(root).startswith("/tmp/"):
        parser.error("--repo must be a disposable /tmp copy; original repos are forbidden")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    selected = [case for case in cases if case["adopter"] == args.adopter]
    if not selected:
        parser.error("no fixture cases match --adopter")
    source_measurement = source_bytes(root)
    output = {"repository": str(root), "adopter": args.adopter, "condition": args.condition,
              "method": "deterministic-retrieval-only",
              "source_bytes_available": source_measurement["bytes_measured"],
              "source_measurement": source_measurement,
              "files_considered": source_measurement["files_considered"],
              "files_measured": source_measurement["files_measured"],
              "bytes_measured": source_measurement["bytes_measured"],
              "truncated": source_measurement["truncated"],
              "truncation_reason": source_measurement["truncation_reason"],
              "token_usage": None,
              "token_note": "no model called; bytes/calls are reported separately",
              "results": []}
    for case in selected:
        question = case["question"]
        if args.condition == "task-handoff":
            question += "\nUse the current Task/Handoff if present, and verify source evidence."
        # One global budget for all matching paths; no per-file -m illusion.
        measurement = bounded_retrieval(root, case["retrieval_pattern"])
        measurement.update({"case": case["id"], "question": question,
                            "command_count": 1, "condition": args.condition})
        measurement.update(evaluate(measurement.get("stdout", ""), case["gold_paths"]))
        measurement["recall_complete"] = measurement["retrieval_complete"]
        measurement["recall_status"] = "complete" if measurement["recall_complete"] else "partial"
        output["results"].append(measurement)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(output["results"]), "method": output["method"],
                      "successful_cases": sum(r["recall_complete"] and r["recall_count"] == r["gold_count"] for r in output["results"]),
                      "output": str(args.out)}))


if __name__ == "__main__":
    main()
