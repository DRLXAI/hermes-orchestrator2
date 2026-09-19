"""Render a calibration run as a memo somebody can act on or forward.

The buyer of this kit usually has to convince one other person -- a tech lead,
a CTO, whoever owns the budget -- that switching a production classifier to Jev
is safe. That argument is lost with a notebook and won with a page that states
what was measured, on how many labels, what it costs, and what the worst case
is. So the report leads with the decision and keeps the statistics underneath
it, rather than the other way round.
"""

from __future__ import annotations

from .profile import Profile

_RULE = "=" * 78


def _direction_note(direction: str) -> str:
    return {
        "overconfident": "raw gate would auto-act on more errors than it implies",
        "underconfident": "raw gate would over-escalate and waste the savings",
        "distorted": "errors cancel in the mean but not in the bands",
        "calibrated": "raw gate is defensible",
        "unmeasurable": "not enough labels to tell",
    }.get(direction, direction)


def profile_report(profile: Profile) -> str:
    """One question's findings, decision first."""
    lines = [_RULE, f"  {profile.question}   [{profile.kind}]", _RULE]

    if not profile.automates:
        lines += [
            "",
            "  DECISION:  DO NOT AUTOMATE THIS QUESTION",
            "",
            f"  No confidence cutoff beats sending every case to review at "
            f"${profile.economics['cost_review']:,.2f}.",
            f"  An error costs ${profile.economics['cost_error']:,.2f}, which is "
            f"{profile.economics['cost_error'] / max(profile.economics['cost_review'], 1e-9):.0f}x "
            f"a review,",
            "  so at this accuracy the automatic path cannot pay for its own mistakes.",
            "",
            "  Try: split it into narrower questions, or automate a cheaper question first.",
        ]
    else:
        lines += [
            "",
            f"  DECISION:  auto-act when calibrated probability >= {profile.threshold:.2f}",
            "",
            f"  Covers          {profile.coverage:>7.1%} of traffic",
            f"  Precision       {profile.precision:>7.2%}   "
            f"(worst case {profile.precision_lower:.2%})",
            f"  Cost            ${profile.cost_per_1k:>7.2f} per 1,000 decisions",
            "",
            f"  What `if answer.confidence >= 0.9` would have done instead:",
            f"    covers {profile.naive_coverage:.1%} at {profile.naive_precision:.2%} precision, "
            f"${profile.naive_cost_per_1k:,.2f} per 1,000",
            f"    -> the fitted threshold saves ${profile.saving_per_1k:,.2f} per 1,000",
        ]

    lines += [
        "",
        "  CALIBRATION",
        f"    raw          ECE {profile.raw_ece:.4f}   {profile.raw_direction}"
        f"  ({_direction_note(profile.raw_direction)})",
        f"    calibrated   ECE {profile.calibrated_ece:.4f}   {profile.calibrated_direction}",
        f"    map          {profile.map_kind}, fitted on {profile.n_fit}, "
        f"verified on {profile.n_verify} held-out",
        f"    model        {profile.model}",
        "",
    ]
    return "\n".join(lines)


def summary(profiles: list[Profile], *, monthly_volume: int = 0) -> str:
    """The portfolio view: what to switch on, what to leave alone, what it saves."""
    if not profiles:
        return "No profiles fitted."

    automatable = [p for p in profiles if p.automates]
    blocked = [p for p in profiles if not p.automates]

    lines = [_RULE, "  JEV THRESHOLD REPORT", _RULE, ""]
    lines.append(f"  {len(profiles)} question(s) measured. "
                 f"{len(automatable)} safe to automate, {len(blocked)} not.")
    lines.append("")
    lines.append(
        f"  {'question':<20}{'fitted':<10}{'cover':>7}{'prec':>8}"
        f"{'$/1k':>9}   |{'raw 0.9 cover':>15}{'prec':>8}{'$/1k':>9}"
    )
    lines.append(f"  {'-' * 88}")
    for p in profiles:
        left = (
            f"  {p.question:<20}{'>= ' + format(p.threshold, '.2f'):<10}"
            f"{p.coverage:>7.1%}{p.precision:>8.2%}{p.cost_per_1k:>9.2f}"
            if p.automates
            else f"  {p.question:<20}{'ESCALATE':<10}{'-':>7}{'-':>8}{p.cost_per_1k:>9.2f}"
        )
        lines.append(
            f"{left}   |{p.naive_coverage:>15.1%}{p.naive_precision:>8.2%}"
            f"{p.naive_cost_per_1k:>9.2f}"
        )
    lines.append("")

    if monthly_volume > 0:
        spend = sum(p.cost_per_1k for p in profiles) * monthly_volume / 1000.0
        naive_spend = sum(p.naive_cost_per_1k for p in profiles) * monthly_volume / 1000.0
        saved = naive_spend - spend
        lines += [
            f"  At {monthly_volume:,} decisions per question per month "
            f"({len(profiles) * monthly_volume:,} decisions total):",
            f"    this policy                    ${spend:>12,.2f}/mo",
            f"    a raw 0.90 cutoff              ${naive_spend:>12,.2f}/mo",
            f"    difference                     ${saved:>12,.2f}/mo",
            "",
        ]

    risky = [p for p in profiles if p.raw_direction == "overconfident"]
    if risky:
        lines += [
            "  WARNING -- these were overconfident before calibration:",
            f"    {', '.join(p.question for p in risky)}",
            "    Shipping a raw `confidence >= 0.9` gate on these auto-acts on errors",
            "    at a rate the number does not disclose. The fitted map is load-bearing.",
            "",
        ]

    thin = [p for p in profiles if p.n_verify < 100]
    if thin:
        lines += [
            "  CAUTION -- verified on under 100 held-out items:",
            f"    {', '.join(f'{p.question} (n={p.n_verify})' for p in thin)}",
            "    Treat these thresholds as provisional until you have more labels.",
            "",
        ]

    lines.append(_RULE)
    return "\n".join(lines)
