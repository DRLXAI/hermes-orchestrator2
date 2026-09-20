"""The human review workflow: approvals, and outcome confirmation.

Two queues, deliberately separate, because the two acts mean different things:

  * APPROVAL    -- "you may proceed". Satisfies a REQUIRE_APPROVAL verdict. Says nothing about
                   whether the decision was right.
  * OUTCOME     -- "this turned out true/false". The only admissible label. Says nothing about
                   whether anybody approved it.

A reviewer agreeing with a recommendation is concordance and is recorded nowhere near a label.
"""

from __future__ import annotations

from dataclasses import dataclass

from .store import EvidenceStore
from .verdict import Verdict


@dataclass(frozen=True)
class PendingItem:
    correlation_id: str
    action: str
    verdict: str
    question: str = ""
    summary: str = ""


def _decision_rows(store: EvidenceStore) -> list:
    return [r for r in store.all_rows() if r.kind == "decision"]


def pending_approvals(store: EvidenceStore) -> list[PendingItem]:
    """Decisions that reached REQUIRE_APPROVAL and have no trusted approval yet."""
    out = []
    for row in _decision_rows(store):
        if row.payload.get("final") != str(Verdict.REQUIRE_APPROVAL):
            continue
        if store.approved(row.correlation_id):
            continue
        out.append(
            PendingItem(
                correlation_id=row.correlation_id,
                action=str(row.payload.get("action", "")),
                verdict=str(row.payload.get("final", "")),
                summary=", ".join(row.payload.get("resources") or [])[:80],
            )
        )
    return out


def pending_outcomes(store: EvidenceStore) -> list[PendingItem]:
    """Decisions with model advice but no confirmed ground truth.

    These are what an operator works through to turn observations into labels. Nothing here is
    a label until a trusted confirmer says so.
    """
    laboured = {r.correlation_id for r in store.labelled()}
    seen: dict[str, PendingItem] = {}
    for row in store.all_rows():
        if row.kind != "advice" or row.correlation_id in laboured:
            continue
        seen[row.correlation_id] = PendingItem(
            correlation_id=row.correlation_id,
            action="",
            verdict=str(row.payload.get("verdict", "")),
            question=str(row.payload.get("question", "")),
            summary=f"answer={row.payload.get('answer')!r} confidence={row.payload.get('confidence')}",
        )
    for row in _decision_rows(store):
        if row.correlation_id in seen:
            item = seen[row.correlation_id]
            seen[row.correlation_id] = PendingItem(
                correlation_id=item.correlation_id,
                action=str(row.payload.get("action", "")),
                verdict=item.verdict,
                question=item.question,
                summary=item.summary,
            )
    return list(seen.values())


def approve(store: EvidenceStore, correlation_id: str, *, approver: str, granted: bool = True):
    """Record a human approval. Raises unless the approver is a configured trusted source."""
    return store.record_approval(correlation_id, approver=approver, granted=granted)


def confirm_outcome(
    store: EvidenceStore, correlation_id: str, *, ground_truth: bool, confirmed_by: str,
    question: str = "", confidence: float | None = None,
):
    """Record ground truth. Raises unless the confirmer is a configured trusted source.

    If `question`/`confidence` are omitted they are carried from the advice row, so a label
    lands on the same question the model was asked.
    """
    if not question or confidence is None:
        advice = next(
            (r for r in store.chain(correlation_id) if r.kind == "advice"), None
        )
        if advice is not None:
            question = question or str(advice.payload.get("question", ""))
            if confidence is None:
                raw = advice.payload.get("confidence")
                confidence = float(raw) if raw is not None else None
    return store.record_outcome(
        correlation_id, ground_truth=ground_truth, confirmed_by=confirmed_by,
        question=question, confidence=confidence,
    )
