"""Append-only evidence, and the labelled/observed distinction that calibration depends on.

Two things are kept rigorously apart, because conflating them is the most common way a
calibration number becomes fiction:

  A. `operator_agreed` -- a human agreed with a recommendation. This is CONCORDANCE. Calibrating
     on it fits a model of the operator, not a model of correctness.
  B. `ground_truth`    -- what actually turned out to be true, confirmed by a human or by a
     deterministic trusted source. This is the only thing admissible as a label.

`ground_truth is None` means UNKNOWN. It is never read as False, and an unlabelled row can
never reach a calibration export.

This module deliberately does NOT decide how many labels are enough. It reports what it has and
refuses to conclude. Picking a sufficiency target is the operator's call, and inventing one here
and calling it statistically valid would be exactly the failure this product exists to prevent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterator, Sequence

from .verdict import Verdict


@dataclass(frozen=True)
class Observation:
    """One recorded decision. Shadow mode records these without acting on the advice."""

    correlation_id: str
    action: str
    ceiling: Verdict
    final: Verdict
    adapter: str = ""
    model: str = ""
    question: str = ""
    answer: str = ""
    confidence: float | None = None
    #: The deterministic expectation where one exists; None means there was none.
    deterministic_expected: str | None = None
    #: Concordance only. NEVER a label.
    operator_agreed: bool | None = None
    #: The only admissible label. None means UNKNOWN.
    ground_truth: bool | None = None
    #: Who or what confirmed ground_truth. Empty while unlabelled.
    confirmed_by: str = ""
    evidence_refs: tuple[str, ...] = ()
    recorded_at: str = ""

    @property
    def labelled(self) -> bool:
        """A row is labelled only with real ground truth AND a named confirmer."""
        return self.ground_truth is not None and bool(self.confirmed_by)

    @property
    def false_safe(self) -> bool:
        """The dangerous direction: the system allowed something that was in fact wrong.

        Unknown ground truth is not a false-safe. It is unknown.
        """
        return self.labelled and self.ground_truth is False and self.final is Verdict.ALLOW


@dataclass
class EvidenceLog:
    """Append-only. Rows are never mutated; a label arrives as a new row that supersedes."""

    _rows: list[Observation] = field(default_factory=list)

    def record(self, observation: Observation) -> Observation:
        self._rows.append(observation)
        return observation

    def confirm_outcome(
        self, correlation_id: str, *, ground_truth: bool, confirmed_by: str
    ) -> Observation:
        """Append a labelled successor. Requires a named confirmer: no anonymous labels."""
        if not confirmed_by.strip():
            raise ValueError("a label needs a named human or trusted deterministic source")
        prior = self.latest(correlation_id)
        if prior is None:
            raise KeyError(f"no observation for correlation id {correlation_id!r}")
        from dataclasses import replace

        return self.record(replace(prior, ground_truth=ground_truth, confirmed_by=confirmed_by))

    def latest(self, correlation_id: str) -> Observation | None:
        for row in reversed(self._rows):
            if row.correlation_id == correlation_id:
                return row
        return None

    def __iter__(self) -> Iterator[Observation]:
        return iter(tuple(self._rows))

    def __len__(self) -> int:
        return len(self._rows)

    # ------------------------------------------------------------------ views

    def observed(self) -> list[Observation]:
        """Every row, labelled or not."""
        return list(self._rows)

    def labelled(self) -> list[Observation]:
        """Only rows carrying genuine, attributed ground truth."""
        by_id: dict[str, Observation] = {}
        for row in self._rows:
            if row.labelled:
                by_id[row.correlation_id] = row
        return list(by_id.values())

    def export_for_calibration(self, question: str | None = None) -> list[dict[str, Any]]:
        """Rows a calibration tool (e.g. jevcal) may consume.

        Only labelled rows, and `operator_agreed` is stripped so no downstream tool can mistake
        concordance for correctness.
        """
        rows: list[dict[str, Any]] = []
        for row in self.labelled():
            if question is not None and row.question != question:
                continue
            if row.confidence is None:
                continue  # nothing to calibrate against
            payload = asdict(row)
            payload.pop("operator_agreed", None)
            payload["ceiling"] = str(row.ceiling)
            payload["final"] = str(row.final)
            rows.append(payload)
        return rows

    def readiness(self, question: str, *, required_labelled: int | None = None) -> "Readiness":
        """What evidence exists. Concludes nothing unless the operator supplies a target."""
        observed = [r for r in self._rows if r.question == question]
        labelled = [r for r in self.labelled() if r.question == question]
        false_safe = [r for r in labelled if r.false_safe]
        disagreed = [
            r for r in labelled if r.operator_agreed is False
        ]
        return Readiness(
            question=question,
            observed_count=len(observed),
            labelled_count=len(labelled),
            false_safe_count=len(false_safe),
            disagreement_count=len(disagreed),
            required_labelled=required_labelled,
        )


@dataclass(frozen=True)
class Readiness:
    """Evidence counts. `can_conclude` is False until an operator states a target."""

    question: str
    observed_count: int
    labelled_count: int
    false_safe_count: int
    disagreement_count: int
    required_labelled: int | None = None

    @property
    def can_conclude(self) -> bool:
        if self.required_labelled is None:
            return False
        return self.labelled_count >= self.required_labelled

    @property
    def reason(self) -> str:
        if self.required_labelled is None:
            return (
                f"{self.labelled_count} labelled of {self.observed_count} observed; no sufficiency "
                "target has been set, so no calibration conclusion is available"
            )
        if not self.can_conclude:
            return (
                f"insufficient evidence: {self.labelled_count} labelled, "
                f"{self.required_labelled} required"
            )
        return f"{self.labelled_count} labelled rows meet the stated target"

    @property
    def false_safe_rate(self) -> float | None:
        """None when there is nothing to divide by. Never 0.0 by default."""
        if not self.labelled_count:
            return None
        return self.false_safe_count / self.labelled_count
