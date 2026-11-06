from __future__ import annotations

"""Conservative, warning-only lint for low-information Task chronologies."""

import re
import unicodedata
from collections import Counter
from typing import Any


_BULLET = re.compile(r"^-\s+(.+)$")
_STAMP = re.compile(r"^(?:\d{8}T\d{6}Z:\s*)")
_OPAQUE_ID = re.compile(r"\b(?:[A-Z]-\d{8,}Z(?:--[a-z0-9-]+)?|[a-f0-9]{20,})\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_NONWORD = re.compile(r"[^\w\s]", re.UNICODE)


def _log_bullets(section: str) -> list[tuple[int, str]]:
    """Extract top-level authored bullets, excluding fenced examples."""
    entries: list[tuple[int, str]] = []
    fence = ""
    for line_number, line in enumerate(section.splitlines(), 1):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if not fence:
                fence = marker
            elif fence == marker:
                fence = ""
            continue
        if fence:
            continue
        match = _BULLET.match(line)
        if match:
            entries.append((line_number, _STAMP.sub("", match.group(1))))
        elif entries and line.startswith(("  ", "\t")) and stripped:
            previous_line, previous_text = entries[-1]
            entries[-1] = previous_line, previous_text + " " + stripped
    return entries


def _fingerprint(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = _OPAQUE_ID.sub(" ", normalized)
    normalized = _NUMBER.sub(" ", normalized)
    normalized = _NONWORD.sub(" ", normalized)
    return " ".join(normalized.split())


def semantic_log_repetition(section: str) -> list[dict[str, Any]]:
    """Flag only substantial, high-frequency repetition; never rewrite prose."""
    entries = _log_bullets(section)
    if len(entries) < 12:
        return []
    fingerprints = [_fingerprint(text) for _, text in entries]
    eligible = [
        key for key in fingerprints
        if len(key) >= 24 and len(key.split()) >= 5
    ]
    if len(eligible) < 12:
        return []
    counts = Counter(eligible)
    repeated, count = counts.most_common(1)[0]
    unique_ratio = len(counts) / len(eligible)
    if count < 10 or count / len(entries) < 0.6 or unique_ratio >= 0.5:
        return []
    sample_line, sample_text = next(
        (line, content)
        for line, content in entries
        if _fingerprint(content) == repeated
    )
    return [{
        "code": "task_log_semantic_repetition",
        "message": "Execution Log repeats low-information checkpoints; keep meaningful changes, causes and results (warning only)",
        "count": count,
        "sample_line": sample_line,
        "sample": sample_text[:160],
        "unique_information_ratio": round(unique_ratio, 3),
    }]
