from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LanguageProfile:
    id: str
    suffixes: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    semantic_source: bool = False


LANGUAGE_PROFILES: tuple[LanguageProfile, ...] = (
    LanguageProfile(
        id="python",
        suffixes=(".py",),
        exclude_patterns=(".venv/**", "venv/**", "env/**", ".pytest_cache/**", ".mypy_cache/**", ".ruff_cache/**", ".tox/**", ".nox/**", "*.egg-info/**", "__pycache__/**", "**/__pycache__/**", "*.pyc", "*.pyo", "**/*.pyc", "**/*.pyo"),
        semantic_source=True,
    ),
    LanguageProfile(
        id="javascript",
        suffixes=(".js", ".jsx", ".mjs", ".cjs"),
        exclude_patterns=("node_modules/**", ".next/**", ".nuxt/**", ".svelte-kit/**", ".turbo/**", ".parcel-cache/**", ".firebase/**", ".playwright-browsers/**", "dist/**", "build/**", "coverage/**", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb"),
        semantic_source=True,
    ),
    LanguageProfile(
        id="typescript",
        suffixes=(".ts", ".tsx", ".mts", ".cts"),
        exclude_patterns=("node_modules/**", ".next/**", ".nuxt/**", ".svelte-kit/**", ".turbo/**", ".parcel-cache/**", ".firebase/**", ".playwright-browsers/**", "dist/**", "build/**", "coverage/**", "*.tsbuildinfo", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb"),
        semantic_source=True,
    ),
    LanguageProfile(
        id="dart",
        suffixes=(".dart",),
        exclude_patterns=(".dart_tool/**", "build/**", ".flutter-plugins", ".flutter-plugins-dependencies"),
        semantic_source=True,
    ),
    LanguageProfile(
        id="csharp",
        suffixes=(".cs",),
        exclude_patterns=("Library/**", "Temp/**", "Obj/**", "obj/**", "bin/**", "Build/**", "Builds/**", "Logs/**", "UserSettings/**", "MemoryCaptures/**"),
        semantic_source=True,
    ),
    LanguageProfile(id="go", suffixes=(".go",), exclude_patterns=("vendor/**", "bin/**", "coverage/**"), semantic_source=True, ),
    LanguageProfile(id="rust", suffixes=(".rs",), exclude_patterns=("target/**", "coverage/**"), semantic_source=True, ),
    LanguageProfile(id="java", suffixes=(".java", ".kt", ".kts"), exclude_patterns=(".gradle/**", "build/**", "target/**", "out/**"), semantic_source=True, ),
    LanguageProfile(id="swift", suffixes=(".swift",), exclude_patterns=(".build/**", "DerivedData/**"), semantic_source=True, ),
    LanguageProfile(id="shell", suffixes=(".sh", ".bash", ".zsh"), semantic_source=True, ),
    LanguageProfile(id="sql", suffixes=(".sql",), semantic_source=True, ),
    LanguageProfile(id="html", suffixes=(".html", ".htm"), ),
    LanguageProfile(id="stylesheet", suffixes=(".css", ".scss", ".sass", ".less"), ),
    LanguageProfile(id="markdown", suffixes=(".md", ".markdown"), ),
    LanguageProfile(id="json", suffixes=(".json",), ),
    LanguageProfile(id="toml", suffixes=(".toml",), ),
    LanguageProfile(id="yaml", suffixes=(".yaml", ".yml"), ),
)

COMMON_EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".git/**",
    ".repoctl-state/**",
    ".cache/**",
    ".gstack/**",
    "*.lock",
    "*.log",
    "**/*.log",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.webp",
    "**/*.zip",
    "**/*.tar",
    "**/*.gz",
)

LANGUAGE_BY_SUFFIX = {suffix: profile.id for profile in LANGUAGE_PROFILES for suffix in profile.suffixes}
PROFILE_BY_ID = {profile.id: profile for profile in LANGUAGE_PROFILES}

# Dart `part`/`export` are source dependencies as well as `import`.
# `part of` declares the reverse library identity and is never an import.
_DART_DIRECTIVE_PREFIX = re.compile(
    r"(?m)^[ \t]*(part[ \t]+of|import|export|part)[ \t]+"
)
_DART_DIRECTIVE_URI = re.compile(
    r"^[ \t]*(?P<kind>part[ \t]+of|import|export|part)[ \t]+"
    r"(?P<quote>['\"])(?P<uri>[^'\"\r\n]+)(?P=quote)"
)


def dart_directives(text: str) -> list[tuple[str, str]]:
    """Conservatively recognize Dart URI directives in source, not comments.

    This is a bounded lexical scanner, not a compiler: exact literal paths
    are useful for navigation, while imports requiring type/URI configuration
    remain unresolved. Commented-out directives and triple-quoted fixtures
    must never create confirmed Graph file relationships.
    """
    mask = list(text)
    length = len(text)
    index = 0

    def blank(start: int, end: int) -> None:
        for at in range(start, end):
            if text[at] not in "\r\n":
                mask[at] = " "

    while index < length:
        if text.startswith("//", index):
            end = text.find("\n", index)
            if end < 0:
                end = length
            blank(index, end)
            index = end
            continue
        if text.startswith("/*", index):
            depth = 1
            cursor = index + 2
            while cursor < length and depth:
                if text.startswith("/*", cursor):
                    depth += 1
                    cursor += 2
                elif text.startswith("*/", cursor):
                    depth -= 1
                    cursor += 2
                else:
                    cursor += 1
            blank(index, cursor)
            index = cursor
            continue
        if text[index] in "\"'":
            quote = text[index]
            triple = text.startswith(quote * 3, index)
            marker = quote * (3 if triple else 1)
            cursor = index + len(marker)
            while cursor < length:
                if text[cursor] == "\\" and cursor + 1 < length:
                    cursor += 2
                    continue
                if text.startswith(marker, cursor):
                    cursor += len(marker)
                    break
                if not triple and text[cursor] in "\r\n":
                    break
                cursor += 1
            blank(index, cursor)
            index = cursor
            continue
        index += 1

    visible = "".join(mask)
    directives: list[tuple[str, str]] = []
    for prefix in _DART_DIRECTIVE_PREFIX.finditer(visible):
        line_end = text.find("\n", prefix.start())
        line = text[prefix.start():line_end if line_end >= 0 else length]
        match = _DART_DIRECTIVE_URI.match(line)
        if not match:
            continue
        kind = " ".join(match.group("kind").split())
        uri = match.group("uri")
        if kind.startswith("part") and not re.match(r"[ \t]*;", line[match.end():]):
            continue
        directives.append((kind, uri))
    return directives


def default_indexing_excludes() -> list[str]:
    patterns: list[str] = [*COMMON_EXCLUDE_PATTERNS]
    for profile in LANGUAGE_PROFILES:
        patterns.extend(profile.exclude_patterns)
    return _dedupe(patterns)


def language_for_path(path: str) -> str:
    name = Path(path).name
    lowered_name = name.casefold()
    if lowered_name == "dockerfile" or lowered_name.startswith("dockerfile."):
        return "dockerfile"
    return LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), "unknown")


def is_semantic_source_language(language: str) -> bool:
    profile = PROFILE_BY_ID.get(language)
    return bool(profile and profile.semantic_source)


def index_dart(text: str) -> tuple[list[str], list[str], list[str], str, str]:
    imports = [uri for kind, uri in dart_directives(text) if kind != "part of"]
    return [], _dedupe(imports), [], "ok", ""


def _dedupe(values: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
