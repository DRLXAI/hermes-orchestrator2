"""Git effect observer: what actually changed in a repository, determined independently.

This reads the repository with git itself. It never accepts the agent's account of what it
touched -- the agent's declaration is at most a claim to be CONTRADICTED by this evidence.

What it establishes, given a trusted repo path and a trusted baseline commit:

    repository identity (root commit)   -- is this even the repo we authorised work in?
    baseline reachability               -- does the baseline we recorded still exist here?
    added / modified / deleted / renamed / type-changed files
    symlinks added or introduced        -- a scope-escape primitive, flagged regardless of path
    working-tree cleanliness            -- uncommitted effects the diff would otherwise miss
    resulting head and tree identity

Observation can prove or disprove compliance. It can never grant authority: `assess_git_effect`
returns ALLOW only in the sense of "no objection", and the engine folds that through `narrowest`
against a ceiling computed without it. An observation arriving after the fact cannot retroactively
authorise anything it did not authorise before.

Unavailable evidence is UNKNOWN, never compliant. If git cannot be run, the repo is not the one
expected, or the baseline is unreachable, the result is ESCALATE with a reason -- not a pass.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .. import scope as scope_mod
from ..engine import Finding
from ..verdict import UNKNOWN_VERDICT, Verdict

#: Git's mode for a symbolic link.
SYMLINK_MODE = "120000"
#: Git's mode for a gitlink (submodule): another repository grafted in.
GITLINK_MODE = "160000"


class GitObserverError(RuntimeError):
    """Git could not be consulted. Never silently treated as 'nothing changed'."""


@dataclass(frozen=True)
class GitObservation:
    """What git says happened. `available=False` means UNKNOWN, not clean."""

    available: bool
    reason: str
    repo: str = ""
    identity: str = ""
    baseline: str = ""
    head: str = ""
    tree: str = ""
    added: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    renamed: tuple[tuple[str, str], ...] = ()
    type_changed: tuple[str, ...] = ()
    symlinks: tuple[str, ...] = ()
    submodules: tuple[str, ...] = ()
    dirty: tuple[str, ...] = ()

    @property
    def touched(self) -> tuple[str, ...]:
        """Every path implicated, including BOTH sides of a rename.

        A rename out of scope is two events: something left an authorised path and something
        arrived at an unauthorised one. Counting only the destination would miss half of it.
        """
        paths: list[str] = [*self.added, *self.modified, *self.deleted, *self.type_changed]
        for old, new in self.renamed:
            paths.extend((old, new))
        paths.extend(self.dirty)
        return tuple(dict.fromkeys(paths))

    def as_payload(self) -> dict[str, object]:
        return {
            "available": self.available, "reason": self.reason, "identity": self.identity,
            "baseline": self.baseline, "head": self.head, "tree": self.tree,
            "added": list(self.added), "modified": list(self.modified),
            "deleted": list(self.deleted),
            "renamed": [list(pair) for pair in self.renamed],
            "type_changed": list(self.type_changed), "symlinks": list(self.symlinks),
            "submodules": list(self.submodules), "dirty": list(self.dirty),
            "touched": list(self.touched),
        }


def _unavailable(reason: str, **kw: object) -> GitObservation:
    return GitObservation(available=False, reason=reason, **kw)  # type: ignore[arg-type]


@dataclass
class GitEffectObserver:
    """Reads a git repository directly.

    `repo` and `expected_identity` come from your configuration, NOT from the agent. If you let
    the agent choose which repository is observed, the observation proves nothing.
    """

    repo: Path
    git_binary: str = "git"
    #: The repository's root-commit sha, if you pinned one. A mismatch is refused outright.
    expected_identity: str | None = None
    name: str = "git"
    timeout: float = 60.0

    def _git(self, *args: str) -> str:
        try:
            proc = subprocess.run(
                [self.git_binary, "-C", str(self.repo), *args],
                capture_output=True, text=True, timeout=self.timeout, check=False,
                env={"GIT_OPTIONAL_LOCKS": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitObserverError(f"git could not be run: {exc}") from None
        if proc.returncode != 0:
            raise GitObserverError(
                f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()[:200]}"
            )
        return proc.stdout

    def identity(self) -> str:
        """The root commit: a stable identity for the repository that no remote rename changes."""
        roots = self._git("rev-list", "--max-parents=0", "HEAD").split()
        if not roots:
            raise GitObserverError("repository has no root commit")
        return sorted(roots)[0]

    def observe(self, baseline: str, head: str = "HEAD") -> GitObservation:
        """Determine what changed between `baseline` and `head`, plus any uncommitted effects."""
        try:
            identity = self.identity()
        except GitObserverError as exc:
            return _unavailable(str(exc), repo=str(self.repo))

        if self.expected_identity and identity != self.expected_identity:
            return _unavailable(
                f"repository identity mismatch: expected {self.expected_identity[:12]}, "
                f"found {identity[:12]}",
                repo=str(self.repo), identity=identity,
            )
        try:
            self._git("cat-file", "-e", f"{baseline}^{{commit}}")
        except GitObserverError:
            return _unavailable(
                f"baseline {baseline[:12]} is not reachable in this repository",
                repo=str(self.repo), identity=identity, baseline=baseline,
            )
        try:
            head_sha = self._git("rev-parse", f"{head}^{{commit}}").strip()
            tree_sha = self._git("rev-parse", f"{head}^{{tree}}").strip()
            raw = self._git("diff", "--raw", "-M", "-z", "--no-color", baseline, head)
            status = self._git("status", "--porcelain", "-z")
        except GitObserverError as exc:
            return _unavailable(str(exc), repo=str(self.repo), identity=identity, baseline=baseline)

        added, modified, deleted, type_changed = [], [], [], []
        renamed: list[tuple[str, str]] = []
        symlinks, submodules = [], []

        tokens = [t for t in raw.split("\0") if t != ""]
        i = 0
        while i < len(tokens):
            meta = tokens[i]
            if not meta.startswith(":"):
                i += 1
                continue
            parts = meta[1:].split()
            if len(parts) < 5:
                i += 1
                continue
            src_mode, dst_mode, _, _, code = parts[0], parts[1], parts[2], parts[3], parts[4]
            letter = code[0]
            takes_two = letter in ("R", "C")
            path = tokens[i + 1] if i + 1 < len(tokens) else ""
            other = tokens[i + 2] if takes_two and i + 2 < len(tokens) else ""
            i += 3 if takes_two else 2

            final_path = other or path
            if SYMLINK_MODE in (src_mode, dst_mode) and letter != "D":
                symlinks.append(final_path)
            if GITLINK_MODE in (src_mode, dst_mode):
                submodules.append(final_path)

            if letter == "A":
                added.append(path)
            elif letter == "M":
                modified.append(path)
            elif letter == "D":
                deleted.append(path)
            elif letter == "T":
                type_changed.append(path)
            elif letter in ("R", "C"):
                renamed.append((path, other))

        dirty = []
        for entry in (e for e in status.split("\0") if e.strip()):
            dirty.append(entry[3:] if len(entry) > 3 else entry)

        return GitObservation(
            available=True,
            reason=f"observed {baseline[:12]}..{head_sha[:12]} in {self.repo}",
            repo=str(self.repo), identity=identity, baseline=baseline, head=head_sha,
            tree=tree_sha, added=tuple(added), modified=tuple(modified), deleted=tuple(deleted),
            renamed=tuple(renamed), type_changed=tuple(type_changed),
            symlinks=tuple(dict.fromkeys(symlinks)), submodules=tuple(dict.fromkeys(submodules)),
            dirty=tuple(dirty),
        )


def assess_git_effect(
    observation: GitObservation,
    authorised_scope: Sequence[str] | None,
    *,
    declared_resources: Sequence[str] | None = None,
    allow_dirty: bool = False,
) -> Finding:
    """Compare an observation against the authorised scope.

    Returns ALLOW only as "no objection". It is folded through `narrowest` by the caller, so it
    can confirm compliance but never confer authority that policy did not already grant.
    """
    if not observation.available:
        return Finding("git_effect", UNKNOWN_VERDICT, f"effect unverifiable: {observation.reason}")

    if observation.submodules:
        return Finding(
            "git_effect", Verdict.STOP,
            f"submodule/gitlink introduced ({', '.join(observation.submodules[:3])}): "
            "another repository grafted in is outside any path scope",
        )
    if observation.symlinks:
        return Finding(
            "git_effect", Verdict.STOP,
            f"symlink written ({', '.join(observation.symlinks[:3])}): a link is a scope-escape "
            "primitive regardless of where the link itself sits",
        )
    if observation.dirty and not allow_dirty:
        return Finding(
            "git_effect", Verdict.STOP,
            f"{len(observation.dirty)} uncommitted change(s) in the working tree "
            f"({', '.join(observation.dirty[:3])}): effects outside the committed diff",
        )
    if authorised_scope is None:
        return Finding(
            "git_effect", UNKNOWN_VERDICT,
            "no authorised scope to compare the observed effect against",
        )
    try:
        patterns = scope_mod.normalize(authorised_scope)
    except scope_mod.ScopeError as exc:
        return Finding("git_effect", Verdict.STOP, f"authorised scope is unusable: {exc}")

    stray = scope_mod.outside(observation.touched, patterns)
    if stray:
        return Finding(
            "git_effect", Verdict.STOP,
            f"{len(stray)} path(s) changed outside the authorised scope: "
            f"{', '.join(sorted(stray)[:5])}",
        )
    if declared_resources is not None:
        declared = set(declared_resources)
        observed = set(observation.touched)
        undeclared = sorted(observed - declared)
        if undeclared:
            return Finding(
                "git_effect", Verdict.STOP,
                f"the agent declared {len(declared)} resource(s) but git shows "
                f"{len(observed)}; undeclared: {', '.join(undeclared[:5])}",
            )
    return Finding(
        "git_effect", Verdict.ALLOW,
        f"{len(observation.touched)} observed path(s), all inside the authorised scope",
    )
