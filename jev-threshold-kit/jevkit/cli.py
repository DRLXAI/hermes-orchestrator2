"""Command line for the kit: fit, report and check without writing Python.

    jevkit fit      labels.jsonl --error 8 --review 0.60 --out calibration/
    jevkit report   calibration/ --volume 400000
    jevkit check    calibration/ --model typesafe/jev-1.14-20261101
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .guard import check as drift_check
from .profile import Profile, fit
from .report import profile_report, summary
from .thresholds import Economics

OK, FINDINGS, INCOMPLETE = 0, 2, 3


def load_labels(path: Path) -> dict[str, tuple[list[float], list[bool], str]]:
    """Read a JSONL label file into per-question confidence/outcome columns.

    Each line is one decision:

        {"question": "department", "kind": "choice",
         "confidence": 0.82, "correct": true}

    Returns:
        question -> (confidences, correct, kind)

    Raises:
        ValueError: On a malformed line, naming the line number.
    """
    columns: dict[str, tuple[list[float], list[bool], str]] = {}
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
            question = str(row["question"])
            confidence = float(row["confidence"])
            correct = bool(row["correct"])
            kind = str(row.get("kind", "choice"))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{path}:{number}: {error}") from error
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"{path}:{number}: confidence {confidence} is outside [0, 1]")
        bucket = columns.setdefault(question, ([], [], kind))
        bucket[0].append(confidence)
        bucket[1].append(correct)
    return columns


def command_fit(arguments: argparse.Namespace) -> int:
    path = Path(arguments.labels)
    if not path.exists():
        print(f"no such label file: {path}", file=sys.stderr)
        return INCOMPLETE
    try:
        columns = load_labels(path)
    except ValueError as error:
        print(f"malformed labels: {error}", file=sys.stderr)
        return INCOMPLETE
    if not columns:
        print(f"{path} contains no labelled decisions", file=sys.stderr)
        return INCOMPLETE

    economics = Economics(
        cost_error=arguments.error,
        cost_review=arguments.review,
        cost_per_call=arguments.call_cost,
    )
    out = Path(arguments.out)
    profiles: list[Profile] = []
    skipped: list[str] = []
    for question, (confidences, correct, kind) in sorted(columns.items()):
        try:
            profile = fit(
                confidences, correct, economics,
                question=question, kind=kind, model=arguments.model, seed=arguments.seed,
            )
        except ValueError as error:
            skipped.append(f"{question}: {error}")
            continue
        profile.save(out / f"{question}.json")
        profiles.append(profile)
        if arguments.verbose:
            print(profile_report(profile))

    for note in skipped:
        print(f"skipped {note}", file=sys.stderr)
    if not profiles:
        return INCOMPLETE

    print(summary(profiles, monthly_volume=arguments.volume))
    print(f"  {len(profiles)} profile(s) written to {out}/")
    return FINDINGS if any(not p.automates for p in profiles) else OK


def _read_profiles(directory: Path) -> list[Profile]:
    return [Profile.load(p) for p in sorted(directory.glob("*.json"))]


def command_report(arguments: argparse.Namespace) -> int:
    directory = Path(arguments.profiles)
    if not directory.is_dir():
        print(f"no such profile directory: {directory}", file=sys.stderr)
        return INCOMPLETE
    profiles = _read_profiles(directory)
    if not profiles:
        print(f"{directory} contains no profiles", file=sys.stderr)
        return INCOMPLETE
    if arguments.verbose:
        for profile in profiles:
            print(profile_report(profile))
    print(summary(profiles, monthly_volume=arguments.volume))
    return FINDINGS if any(not p.automates for p in profiles) else OK


def command_check(arguments: argparse.Namespace) -> int:
    directory = Path(arguments.profiles)
    if not directory.is_dir():
        print(f"no such profile directory: {directory}", file=sys.stderr)
        return INCOMPLETE
    profiles = _read_profiles(directory)
    if not profiles:
        print(f"{directory} contains no profiles", file=sys.stderr)
        return INCOMPLETE
    drifted = 0
    for profile in profiles:
        result = drift_check(profile, arguments.model)
        print(result.message())
        drifted += int(result.drifted)
    return FINDINGS if drifted else OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jevkit", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    fit_parser = commands.add_parser("fit", help="fit calibration maps and thresholds")
    fit_parser.add_argument("labels", help="JSONL of labelled decisions")
    fit_parser.add_argument("--error", type=float, required=True,
                            help="cost of one wrong auto-action, your currency")
    fit_parser.add_argument("--review", type=float, required=True,
                            help="cost of one escalation to a human or an LLM")
    fit_parser.add_argument("--call-cost", type=float, default=0.00004,
                            help="cost of one Jev call (default: ~1k tokens at list price)")
    fit_parser.add_argument("--out", default="calibration", help="directory for profiles")
    fit_parser.add_argument("--model", default="unknown",
                            help="resolved model id the labels were collected under")
    fit_parser.add_argument("--volume", type=int, default=0, help="monthly decision volume")
    fit_parser.add_argument("--seed", type=int, default=0)
    fit_parser.add_argument("-v", "--verbose", action="store_true")
    fit_parser.set_defaults(handler=command_fit)

    report_parser = commands.add_parser("report", help="render fitted profiles")
    report_parser.add_argument("profiles", nargs="?", default="calibration")
    report_parser.add_argument("--volume", type=int, default=0)
    report_parser.add_argument("-v", "--verbose", action="store_true")
    report_parser.set_defaults(handler=command_report)

    check_parser = commands.add_parser("check", help="detect model drift under thresholds")
    check_parser.add_argument("profiles", nargs="?", default="calibration")
    check_parser.add_argument("--model", required=True,
                              help="model id currently serving, from a live response")
    check_parser.set_defaults(handler=command_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except (OSError, ValueError) as error:
        print(f"jevkit incomplete: {type(error).__name__}: {error}", file=sys.stderr)
        return INCOMPLETE


if __name__ == "__main__":
    raise SystemExit(main())
