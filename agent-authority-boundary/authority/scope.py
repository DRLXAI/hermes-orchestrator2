"""Deterministic resource scope: which resources an action was authorised to touch.

Patterns are relative POSIX-style resource paths. `**` spans segments; `*` and `?` never
cross a `/`. Anything that could widen silently -- absolute paths, `..` traversal, backslashes,
non-strings, blanks -- is refused rather than normalised, because a scope you cannot read
exactly is not a boundary.

A scope is only a boundary if something can fall outside it. A manifest whose UNION matches
every probe below restricts nothing, and is reported as unbounded rather than honoured.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence


class ScopeError(ValueError):
    """A declared scope exists but cannot be used as an authorisation boundary."""


#: Stand-ins for "any name, any depth, including dotfiles and CI paths".
UNBOUNDED_PROBES = (
    "a",
    "a.txt",
    ".hidden",
    "x/b.py",
    "x/y/z/c.bin",
    "src/app/page.tsx",
    ".github/workflows/ci.yml",
)


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Translate one glob into an anchored regex. Segment-aware by construction."""
    out = ["(?s:"]
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    out.append(r")\Z")
    return re.compile("".join(out))


def normalize(raw: object) -> list[str]:
    """Validate a declared scope into usable patterns, or raise ScopeError."""
    if not isinstance(raw, (list, tuple)):
        raise ScopeError(f"scope must be a list of patterns, got {type(raw).__name__}")
    patterns: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise ScopeError(f"scope entry {entry!r} is not a string")
        pattern = entry.strip()
        if not pattern:
            raise ScopeError("scope contains an empty pattern")
        if pattern.startswith("/") or (len(pattern) > 1 and pattern[1] == ":"):
            raise ScopeError(f"scope entry {entry!r} is absolute; patterns are relative")
        if "\\" in pattern:
            raise ScopeError(f"scope entry {entry!r} contains a backslash; use POSIX separators")
        if pattern == ".." or pattern.startswith("../") or "/../" in pattern or pattern.endswith("/.."):
            raise ScopeError(f"scope entry {entry!r} escapes the root")
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def is_unbounded(patterns: Sequence[str]) -> bool:
    """True when the manifest, as a union, places no real restriction."""
    if not patterns:
        return False
    matchers = [compile_pattern(p) for p in patterns]
    return all(any(m.match(probe) for m in matchers) for probe in UNBOUNDED_PROBES)


def outside(resources: Iterable[str], patterns: Sequence[str]) -> list[str]:
    """Resources not matched by any pattern. Empty means every resource is in scope."""
    matchers = [compile_pattern(p) for p in patterns]
    return [r for r in resources if not any(m.match(r) for m in matchers)]
