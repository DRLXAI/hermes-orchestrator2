#!/usr/bin/env python3
"""Mutation battery: invert each critical invariant in the source and confirm the suite notices.

A test suite that passes proves nothing on its own — it has to be capable of failing. This
edits the library in place, runs every suite, counts failures, and restores the file. A
mutation that is NOT caught is a gap in the tests, not a feature of the code.

Run: python3 validation/mutation_battery.py
Exit 0 only if every mutation is caught.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MUTATIONS = [
    ("authority/verdict.py",
     "return Verdict(min(int(v) for v in verdicts))",
     "return Verdict(max(int(v) for v in verdicts))",
     "model output may never increase authority"),
    ("authority/verdict.py",
     "UNKNOWN_VERDICT = Verdict.ESCALATE",
     "UNKNOWN_VERDICT = Verdict.ALLOW",
     "UNKNOWN != FALSE"),
    ("authority/store.py",
     'return "trusted" if source in self.trusted_sources else "untrusted"',
     'return "trusted"',
     "storage does not confer trust"),
    ("authority/store.py",
     'rows = [r for r in self.all_rows() if r.kind == "outcome" and r.trusted]',
     'rows = [r for r in self.all_rows() if r.kind == "outcome"]',
     "OBSERVATION != LABEL"),
    ("authority/store.py",
     "trusted = [r for r in effects if r.trusted]",
     "trusted = list(effects)",
     "DECLARED INTENT != OBSERVED EFFECT"),
    ("authority/observers/git.py",
     'return Finding("git_effect", UNKNOWN_VERDICT, f"effect unverifiable: {observation.reason}")',
     'return Finding("git_effect", Verdict.ALLOW, f"effect unverifiable: {observation.reason}")',
     "unavailable evidence is never compliant"),
    ("authority/observers/git.py",
     "paths.extend((old, new))",
     "paths.extend((new,))",
     "both ends of a rename are observed"),
    ("authority/calibration.py",
     'return self.correction != "none"',
     "return True",
     "no aggregate calibration claim without a correction"),
]

SUITES = [
    "tests/test_adversarial.py", "tests/test_git_observer.py", "tests/test_invariants.py",
    "tests/test_jev_adapter.py", "tests/test_onboarding.py", "tests/test_persistence.py",
]


def failures_across_suites() -> int:
    total = 0
    for suite in SUITES:
        result = subprocess.run(
            [sys.executable, suite], capture_output=True, text=True, cwd=ROOT
        )
        if result.returncode == 0:
            continue
        failed = re.search(r"failures=(\d+)", result.stderr)
        errored = re.search(r"errors=(\d+)", result.stderr)
        counted = (int(failed.group(1)) if failed else 0) + (int(errored.group(1)) if errored else 0)
        total += counted or 1
    return total


def main() -> int:
    print(f"{len(MUTATIONS)} mutations across {len(SUITES)} suites\n")
    uncaught = []
    for path, original, mutated, invariant in MUTATIONS:
        target = ROOT / path
        backup = target.read_text()
        if backup.count(original) != 1:
            print(f"  SKIPPED  anchor not unique in {path}: {original[:45]}")
            uncaught.append(invariant)
            continue
        target.write_text(backup.replace(original, mutated))
        try:
            caught = failures_across_suites()
        finally:
            target.write_text(backup)
        status = "CAUGHT" if caught else "*** NOT CAUGHT ***"
        if not caught:
            uncaught.append(invariant)
        print(f"  {status:18} {caught:>3} failing test(s)  <- {invariant}")

    print()
    if uncaught:
        print("GAP: these invariants can be inverted without the suite noticing:")
        for invariant in uncaught:
            print(f"  - {invariant}")
        return 1
    print("every invariant is defended by at least one test")
    print("NOTE: a low count is a weak defence. Treat 1-2 as a gap, not a pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
