#!/usr/bin/env python3
"""The Git effect observer, against real repositories.

This is the test suite for the product's largest trust claim: that at least one class of effect
can be verified from evidence the agent did not author. Every case here builds an actual git
repository, does something to it, and asks the observer what happened -- never the agent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authority.engine import ActionRequest, decide
from authority.observers.git import (
    GitEffectObserver, GitObservation, assess_git_effect,
)
from authority.policy import load
from authority.verdict import Verdict

GIT = shutil.which("git") or "/usr/bin/git"
ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": "/tmp", "GIT_CONFIG_NOSYSTEM": "1"}
SCOPE = ["docs/**"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [GIT, "-C", str(repo), *args], capture_output=True, text=True, check=True, env=ENV
    ).stdout


class RepoCase(unittest.TestCase):
    """A repository with docs/ authorised and src/ not."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "docs").mkdir()
        (self.repo / "src").mkdir()
        (self.repo / "docs" / "a.md").write_text("one\n")
        (self.repo / "src" / "app.ts").write_text("export const x = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        self.baseline = git(self.repo, "rev-parse", "HEAD").strip()
        self.observer = GitEffectObserver(repo=self.repo, git_binary=GIT)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, message: str = "change") -> None:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)

    def assess(self, *, declared=None, allow_dirty=False):
        observation = self.observer.observe(self.baseline)
        return observation, assess_git_effect(
            observation, SCOPE, declared_resources=declared, allow_dirty=allow_dirty
        )


class CompliantWork(RepoCase):
    def test_an_in_scope_edit_is_confirmed_by_the_observer(self):
        (self.repo / "docs" / "a.md").write_text("two\n")
        self.commit()
        observation, finding = self.assess()
        self.assertTrue(observation.available)
        self.assertEqual(observation.modified, ("docs/a.md",))
        self.assertIs(finding.verdict, Verdict.ALLOW)

    def test_the_observer_reports_the_resulting_tree_identity(self):
        (self.repo / "docs" / "b.md").write_text("new\n")
        self.commit()
        observation, _ = self.assess()
        self.assertEqual(len(observation.head), 40)
        self.assertEqual(len(observation.tree), 40)
        self.assertEqual(observation.identity, self.baseline)  # single-commit history


class OutOfScopeEffects(RepoCase):
    def test_modification_outside_allowed_paths_is_caught(self):
        (self.repo / "src" / "app.ts").write_text("export const x = 2\n")
        self.commit()
        observation, finding = self.assess()
        self.assertIn("src/app.ts", observation.modified)
        self.assertIs(finding.verdict, Verdict.STOP)
        self.assertIn("src/app.ts", finding.reason)

    def test_deletion_outside_allowed_paths_is_caught(self):
        (self.repo / "src" / "app.ts").unlink()
        self.commit()
        observation, finding = self.assess()
        self.assertIn("src/app.ts", observation.deleted)
        self.assertIs(finding.verdict, Verdict.STOP)

    def test_a_new_file_outside_allowed_paths_is_caught(self):
        (self.repo / "src" / "sneaked.ts").write_text("x\n")
        self.commit()
        observation, finding = self.assess()
        self.assertIn("src/sneaked.ts", observation.added)
        self.assertIs(finding.verdict, Verdict.STOP)

    def test_a_rename_out_of_scope_is_caught_by_its_destination(self):
        git(self.repo, "mv", "docs/a.md", "src/a.md")
        self.commit()
        observation, finding = self.assess()
        self.assertIs(finding.verdict, Verdict.STOP)
        self.assertIn("src/a.md", finding.reason)

    def test_a_rename_into_scope_is_caught_by_its_source(self):
        """Both ends of a rename matter: something left an unauthorised path."""
        git(self.repo, "mv", "src/app.ts", "docs/app.ts")
        self.commit()
        observation, finding = self.assess()
        self.assertIn("src/app.ts", observation.touched,
                      "the source of a rename must be counted as touched")
        self.assertIs(finding.verdict, Verdict.STOP)


    def test_a_rename_wholly_inside_scope_is_allowed(self):
        git(self.repo, "mv", "docs/a.md", "docs/b.md")
        self.commit()
        observation, finding = self.assess()
        self.assertIn(("docs/a.md", "docs/b.md"), observation.renamed)
        self.assertIs(finding.verdict, Verdict.ALLOW)

    def test_a_rename_wholly_outside_scope_is_caught(self):
        git(self.repo, "mv", "src/app.ts", "src/moved.ts")
        self.commit()
        _, finding = self.assess()
        self.assertIs(finding.verdict, Verdict.STOP)

    def test_touched_reports_both_ends_of_every_rename(self):
        git(self.repo, "mv", "docs/a.md", "docs/b.md")
        self.commit()
        observation, _ = self.assess()
        self.assertIn("docs/a.md", observation.touched, "the source must be reported")
        self.assertIn("docs/b.md", observation.touched, "the destination must be reported")


class EscapePrimitives(RepoCase):
    def test_a_symlink_escaping_the_repo_is_refused_even_inside_scope(self):
        os.symlink("/etc/passwd", self.repo / "docs" / "link")
        self.commit()
        observation, finding = self.assess()
        self.assertIn("docs/link", observation.symlinks)
        self.assertIs(finding.verdict, Verdict.STOP)
        self.assertIn("symlink", finding.reason)

    def test_a_relative_traversal_symlink_is_refused(self):
        os.symlink("../src/app.ts", self.repo / "docs" / "sneak")
        self.commit()
        _, finding = self.assess()
        self.assertIs(finding.verdict, Verdict.STOP)

    def test_a_file_turned_into_a_symlink_is_refused(self):
        (self.repo / "docs" / "a.md").unlink()
        os.symlink("/etc/hostname", self.repo / "docs" / "a.md")
        self.commit()
        _, finding = self.assess()
        self.assertIs(finding.verdict, Verdict.STOP)


class WorkingTreeEffects(RepoCase):
    def test_uncommitted_changes_are_effects_too(self):
        (self.repo / "docs" / "a.md").write_text("committed\n")
        self.commit()
        (self.repo / "src" / "app.ts").write_text("uncommitted escape\n")
        observation, finding = self.assess()
        self.assertTrue(observation.dirty, "an uncommitted change must be observed")
        self.assertIs(finding.verdict, Verdict.STOP)
        self.assertIn("uncommitted", finding.reason)

    def test_an_untracked_file_is_observed(self):
        (self.repo / "src" / "dropped.ts").write_text("x\n")
        observation, finding = self.assess()
        self.assertTrue(observation.dirty)
        self.assertIs(finding.verdict, Verdict.STOP)

    def test_allow_dirty_still_applies_scope_to_the_dirty_paths(self):
        """Tolerating a dirty tree must not stop checking WHERE it is dirty."""
        (self.repo / "src" / "dropped.ts").write_text("x\n")
        _, finding = self.assess(allow_dirty=True)
        self.assertIs(finding.verdict, Verdict.STOP)
        self.assertIn("src/dropped.ts", finding.reason)


class UnavailableEvidence(RepoCase):
    def test_a_baseline_the_repo_never_had_is_unknown_not_clean(self):
        observation = self.observer.observe("0" * 40)
        finding = assess_git_effect(observation, SCOPE)
        self.assertFalse(observation.available)
        self.assertIs(finding.verdict, Verdict.ESCALATE)
        self.assertIn("not reachable", finding.reason)

    def test_a_repository_identity_mismatch_is_refused(self):
        observer = GitEffectObserver(repo=self.repo, git_binary=GIT, expected_identity="f" * 40)
        observation = observer.observe(self.baseline)
        self.assertFalse(observation.available)
        self.assertIn("identity mismatch", observation.reason)
        self.assertIs(assess_git_effect(observation, SCOPE).verdict, Verdict.ESCALATE)

    def test_a_missing_repository_is_unknown_not_clean(self):
        observer = GitEffectObserver(repo=self.tmp / "nope", git_binary=GIT)
        observation = observer.observe(self.baseline)
        self.assertFalse(observation.available)
        self.assertIs(assess_git_effect(observation, SCOPE).verdict, Verdict.ESCALATE)

    def test_an_unusable_git_binary_is_unknown_not_clean(self):
        observer = GitEffectObserver(repo=self.repo, git_binary="/nonexistent/git")
        observation = observer.observe(self.baseline)
        self.assertFalse(observation.available)
        self.assertIs(assess_git_effect(observation, SCOPE).verdict, Verdict.ESCALATE)

    def test_no_authorised_scope_is_unknown_not_clean(self):
        (self.repo / "docs" / "a.md").write_text("two\n")
        self.commit()
        observation = self.observer.observe(self.baseline)
        self.assertIs(assess_git_effect(observation, None).verdict, Verdict.ESCALATE)

    def test_an_unavailable_observation_never_reports_compliance(self):
        for reason in ("git exploded", "baseline gone", "identity mismatch"):
            finding = assess_git_effect(GitObservation(available=False, reason=reason), SCOPE)
            self.assertIsNot(finding.verdict, Verdict.ALLOW)


class DeclarationVersusObservation(RepoCase):
    """DECLARED INTENT != OBSERVED EFFECT."""

    def test_an_agent_understating_what_it_touched_is_contradicted(self):
        (self.repo / "docs" / "a.md").write_text("two\n")
        (self.repo / "docs" / "secret.md").write_text("also this\n")
        self.commit()
        _, finding = self.assess(declared=["docs/a.md"])
        self.assertIs(finding.verdict, Verdict.STOP)
        self.assertIn("docs/secret.md", finding.reason)

    def test_an_agent_claiming_a_clean_run_that_touched_nothing_declared(self):
        (self.repo / "docs" / "a.md").write_text("two\n")
        self.commit()
        _, finding = self.assess(declared=[])
        self.assertIs(finding.verdict, Verdict.STOP)

    def test_a_truthful_declaration_passes(self):
        (self.repo / "docs" / "a.md").write_text("two\n")
        self.commit()
        _, finding = self.assess(declared=["docs/a.md"])
        self.assertIs(finding.verdict, Verdict.ALLOW)

    def test_the_observer_is_consulted_even_when_the_agent_declares_nothing(self):
        """A silent agent must not be treated as a clean one."""
        (self.repo / "src" / "quiet.ts").write_text("x\n")
        self.commit()
        _, finding = self.assess(declared=None)
        self.assertIs(finding.verdict, Verdict.STOP)

    def test_over_declaring_is_not_itself_a_violation(self):
        """Claiming more than you touched is noise, not an escape. Scope still governs."""
        (self.repo / "docs" / "a.md").write_text("two\n")
        self.commit()
        _, finding = self.assess(declared=["docs/a.md", "docs/never-touched.md"])
        self.assertIs(finding.verdict, Verdict.ALLOW)


class ObservationCannotGrantAuthority(RepoCase):
    """A clean observation confirms compliance. It never lifts a ceiling."""

    def test_a_perfectly_clean_observation_cannot_raise_a_stopped_decision(self):
        policy = load({
            "actions": {"w": {"ceiling": "ALLOW", "scope": SCOPE, "reversible": True}},
            "pinned_models": {}, "trusted_sources": ["policy"],
        })
        blocked = decide(policy, ActionRequest("w", ("src/app.ts",)))
        self.assertIs(blocked.verdict, Verdict.STOP)

        (self.repo / "docs" / "a.md").write_text("two\n")
        self.commit()
        _, finding = self.assess()
        self.assertIs(finding.verdict, Verdict.ALLOW)

        from authority.verdict import narrowest
        self.assertIs(narrowest(blocked.verdict, finding.verdict), Verdict.STOP)

    def test_a_clean_observation_cannot_satisfy_an_approval_requirement(self):
        policy = load({
            "actions": {"d": {"ceiling": "ALLOW", "scope": SCOPE, "reversible": False,
                              "requires_approval": True}},
            "pinned_models": {}, "trusted_sources": ["policy"],
        })
        decision = decide(policy, ActionRequest("d", ("docs/a.md",)))
        (self.repo / "docs" / "a.md").write_text("two\n")
        self.commit()
        _, finding = self.assess()
        from authority.verdict import narrowest
        self.assertIs(narrowest(decision.verdict, finding.verdict), Verdict.REQUIRE_APPROVAL)


if __name__ == "__main__":
    unittest.main(verbosity=1)
