"""`authority` command line. Thin: every decision it prints comes from the library."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import review as review_mod
from .calibration import Target, export_dataset, readiness, write_jsonl
from .engine import ActionRequest, decide, verify_effect
from .policy import PolicyError, load
from .store import EvidenceError, EvidenceStore

EXAMPLE_POLICY = {
    "actions": {
        "write_doc": {
            "ceiling": "ALLOW", "scope": ["docs/**"], "tools": ["read", "write"],
            "reversible": True,
        },
        "deploy": {
            "ceiling": "ALLOW", "scope": ["infra/**"], "tools": ["read", "write", "shell"],
            "reversible": False, "external_side_effects": True, "requires_approval": True,
        },
    },
    "pinned_models": {"jev": "jev-1.13"},
    "trusted_sources": ["policy", "david", "ci-observer"],
}


def _load(path: str):
    try:
        return load(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read policy {path}: {exc}") from None
    except PolicyError as exc:
        raise SystemExit(f"policy refused: {exc}") from None


def _store(policy, db: str) -> EvidenceStore:
    return EvidenceStore.for_policy(policy, db)


def cmd_init(args) -> int:
    target = Path(args.path)
    if target.exists() and not args.force:
        raise SystemExit(f"{target} exists; pass --force to overwrite")
    target.write_text(json.dumps(EXAMPLE_POLICY, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target}")
    print("Edit it, then: authority check " + str(target))
    return 0


def cmd_check(args) -> int:
    policy = _load(args.policy)
    print(f"policy OK  fingerprint={policy.fingerprint}")
    print(f"actions: {len(policy.rules)}  trusted sources: {len(policy.trusted_sources)}")
    for name, rule in sorted(policy.rules.items()):
        scope = ", ".join(rule.scope) if rule.scope else "UNDECLARED"
        tools = ", ".join(rule.tools) if rule.tools else "UNDECLARED"
        print(f"  {name}: ceiling={rule.ceiling} scope=[{scope}] tools=[{tools}] "
              f"reversible={rule.reversible}")
    if not policy.trusted_sources:
        print("warning: no trusted_sources; no approval or outcome can ever be recorded")
    return 0


def cmd_decide(args) -> int:
    policy = _load(args.policy)
    request = ActionRequest(args.action, tuple(args.resource or ()), tuple(args.tool or ()))
    decision = decide(policy, request)
    print(decision.explain())
    if args.db:
        store = _store(policy, args.db)
        store.record_decision(decision)
        print(f"\nrecorded as {decision.request.correlation_id}")
    return 0 if decision.executable else 2


def cmd_review(args) -> int:
    policy = _load(args.policy)
    store = _store(policy, args.db)
    approvals = review_mod.pending_approvals(store)
    outcomes = review_mod.pending_outcomes(store)
    print(f"awaiting approval: {len(approvals)}")
    for item in approvals:
        print(f"  {item.correlation_id}  {item.action}  {item.summary}")
    print(f"awaiting outcome confirmation: {len(outcomes)}")
    for item in outcomes:
        print(f"  {item.correlation_id}  {item.question}  {item.summary}")
    return 0


def cmd_approve(args) -> int:
    policy = _load(args.policy)
    try:
        review_mod.approve(_store(policy, args.db), args.correlation_id, approver=args.by,
                           granted=not args.deny)
    except EvidenceError as exc:
        raise SystemExit(str(exc)) from None
    print(f"{'denied' if args.deny else 'approved'} {args.correlation_id} by {args.by}")
    return 0


def cmd_confirm(args) -> int:
    policy = _load(args.policy)
    try:
        review_mod.confirm_outcome(_store(policy, args.db), args.correlation_id,
                                   ground_truth=not args.false, confirmed_by=args.by)
    except (EvidenceError, KeyError) as exc:
        raise SystemExit(str(exc)) from None
    print(f"outcome recorded for {args.correlation_id} by {args.by}")
    return 0


def cmd_readiness(args) -> int:
    policy = _load(args.policy)
    store = _store(policy, args.db)
    target = None
    if args.max_error is not None:
        target = Target(max_error_rate=args.max_error, confidence=args.confidence)
    result = readiness(store, args.question, target=target)
    print(f"{result.question}: {result.state}")
    print(f"  {result.reason}")
    return 0 if result.state.value != "fails_target" else 2


def cmd_export(args) -> int:
    policy = _load(args.policy)
    rows = export_dataset(_store(policy, args.db), args.question)
    if args.out:
        write_jsonl(rows, args.out)
        print(f"wrote {len(rows)} labelled row(s) to {args.out}")
    else:
        for row in rows:
            print(json.dumps(row, sort_keys=True))
    return 0


def cmd_verify(args) -> int:
    policy = _load(args.policy)
    ok, reason = _store(policy, args.db).verify_chain()
    print(("chain intact: " if ok else "CHAIN BROKEN: ") + reason)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="authority",
        description="Agent Authority Boundary. Models can reduce authority, never create it.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write a starter policy file")
    p.add_argument("path", nargs="?", default="authority-policy.json")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("check", help="validate a policy and print its fingerprint")
    p.add_argument("policy")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("decide", help="evaluate one proposed action")
    p.add_argument("policy")
    p.add_argument("--action", required=True)
    p.add_argument("--resource", action="append")
    p.add_argument("--tool", action="append")
    p.add_argument("--db")
    p.set_defaults(func=cmd_decide)

    for name, func, helptext in (
        ("review", cmd_review, "list what is waiting for a human"),
        ("verify", cmd_verify, "check the evidence chain has not been altered"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("policy")
        p.add_argument("--db", required=True)
        p.set_defaults(func=func)

    p = sub.add_parser("approve", help="record a human approval")
    p.add_argument("policy")
    p.add_argument("correlation_id")
    p.add_argument("--db", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--deny", action="store_true")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("confirm", help="record confirmed ground truth (not agreement)")
    p.add_argument("policy")
    p.add_argument("correlation_id")
    p.add_argument("--db", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--false", action="store_true", help="the outcome was wrong")
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser("readiness", help="evaluate labelled evidence against a target")
    p.add_argument("policy")
    p.add_argument("--db", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--max-error", type=float, help="the operator's acceptable error rate")
    p.add_argument("--confidence", type=float, default=0.95)
    p.set_defaults(func=cmd_readiness)

    p = sub.add_parser("export", help="export labelled rows for a calibration tool")
    p.add_argument("policy")
    p.add_argument("--db", required=True)
    p.add_argument("--question")
    p.add_argument("--out")
    p.set_defaults(func=cmd_export)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
