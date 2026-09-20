"""Append-only evidence store with an explicit trust model.

The rule this module exists to enforce: **writing something into storage does not make it
true.** Every row records who produced it and whether that source is trusted, and trust is
looked up from configuration the agent cannot influence -- never inferred from the payload and
never asserted by the caller at write time.

So a model's advice is stored, and stored as UNTRUSTED. An agent's own account of what it
touched is stored, and stored as UNTRUSTED. Both are evidence *that a claim was made*. Neither
is evidence that the claim is true, and neither can reach a calibration export.

Rows are chained: each row's hash covers the previous row's hash, so deletion or edit of any
row breaks every hash after it. `verify_chain()` detects that. This is tamper-EVIDENCE, not
tamper-proofing: someone with write access to the database can rewrite the whole chain. Keeping
the store somewhere the agent cannot write is still the integrator's job.

The chain a decision reconstructs to:

    trusted policy -> fingerprint -> ceiling -> advisory observation -> final verdict
    -> approval (if required) -> actual effect -> observed outcome -> confirmed ground truth
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .engine import Advice, Decision
from .verdict import Verdict

GENESIS = "0" * 64

#: Row kinds, in the order they occur in a decision's life.
KINDS = ("decision", "advice", "approval", "effect", "outcome")


class EvidenceError(RuntimeError):
    """The evidence chain cannot be trusted or the write is inadmissible."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _row_hash(prev: str, kind: str, correlation_id: str, source: str, trust: str, payload: str) -> str:
    blob = "|".join((prev, kind, correlation_id, source, trust, payload))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Row:
    """One append-only evidence row."""

    id: int
    correlation_id: str
    kind: str
    source: str
    trust: str
    payload: Mapping[str, Any]
    recorded_at: str
    prev_hash: str
    row_hash: str

    @property
    def trusted(self) -> bool:
        return self.trust == "trusted"


SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('decision','advice','approval','effect','outcome')),
    source         TEXT NOT NULL,
    trust          TEXT NOT NULL CHECK (trust IN ('trusted','untrusted')),
    payload_json   TEXT NOT NULL,
    recorded_at    TEXT NOT NULL,
    prev_hash      TEXT NOT NULL,
    row_hash       TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS evidence_correlation ON evidence(correlation_id);
CREATE INDEX IF NOT EXISTS evidence_kind ON evidence(kind);
"""


class EvidenceStore:
    """Append-only, hash-chained evidence.

    `trusted_sources` is the ONLY thing that makes a row trusted. It comes from configuration,
    not from any caller's say-so at write time and never from the payload.
    """

    @classmethod
    def for_policy(cls, policy: Any, path: str | Path = ":memory:") -> "EvidenceStore":
        """Open a store whose trust configuration comes from fingerprinted policy."""
        return cls(path, trusted_sources=policy.trusted_sources)

    def __init__(
        self, path: str | Path = ":memory:", *, trusted_sources: Iterable[str] = ()
    ) -> None:
        self.path = str(path)
        self.trusted_sources = frozenset(s.strip() for s in trusted_sources if s.strip())
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ writing

    def _trust_of(self, source: str, *, never_trusted: bool = False) -> str:
        """Trust is configured, not claimed. Some kinds can never be trusted whatever the source."""
        if never_trusted:
            return "untrusted"
        return "trusted" if source in self.trusted_sources else "untrusted"

    def _append(
        self, correlation_id: str, kind: str, source: str, payload: Mapping[str, Any], *,
        never_trusted: bool = False,
    ) -> Row:
        if kind not in KINDS:
            raise EvidenceError(f"unknown evidence kind {kind!r}")
        if not str(source).strip():
            raise EvidenceError("every evidence row needs a named source")
        trust = self._trust_of(source, never_trusted=never_trusted)
        blob = _canonical(payload)
        prev = self.head_hash()
        digest = _row_hash(prev, kind, correlation_id, source, trust, blob)
        recorded_at = _now()
        cur = self._conn.execute(
            "INSERT INTO evidence (correlation_id, kind, source, trust, payload_json, "
            "recorded_at, prev_hash, row_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (correlation_id, kind, source, trust, blob, recorded_at, prev, digest),
        )
        self._conn.commit()
        return Row(
            id=int(cur.lastrowid or 0), correlation_id=correlation_id, kind=kind, source=source,
            trust=trust, payload=dict(payload), recorded_at=recorded_at, prev_hash=prev,
            row_hash=digest,
        )

    def record_decision(self, decision: Decision, *, source: str = "policy") -> Row:
        """The deterministic decision. Trusted when the configured policy source says so."""
        return self._append(
            decision.request.correlation_id, "decision", source,
            {
                "action": decision.request.action,
                "resources": list(decision.request.resources),
                "ceiling": str(decision.ceiling),
                "final": str(decision.verdict),
                "policy_fingerprint": decision.policy_fingerprint,
                "findings": [
                    {"guard": f.guard, "verdict": str(f.verdict), "reason": f.reason}
                    for f in decision.findings
                ],
            },
        )

    def record_advice(self, correlation_id: str, advice: Advice) -> Row:
        """A model's opinion. ALWAYS untrusted, whatever the adapter is called."""
        return self._append(
            correlation_id, "advice", f"model:{advice.adapter}",
            {
                "adapter": advice.adapter, "model": advice.model, "question": advice.question,
                "answer": advice.answer, "confidence": advice.confidence,
                "confidence_source": advice.confidence_source,
                "verdict": str(advice.verdict),
            },
            never_trusted=True,
        )

    def record_approval(self, correlation_id: str, *, approver: str, granted: bool) -> Row:
        """A human decision. Trusted only if the approver is a configured trusted source.

        As with outcomes, a rejected attempt is recorded as an untrusted row before being
        refused: an agent trying to approve itself is worth keeping in the audit trail.
        """
        row = self._append(
            correlation_id, "approval", approver, {"granted": bool(granted)}
        )
        if not row.trusted:
            raise EvidenceError(
                f"approver {approver!r} is not a configured trusted source; the approval was "
                "recorded as untrusted and does not satisfy a REQUIRE_APPROVAL verdict"
            )
        return row

    def record_effect(
        self, correlation_id: str, *, resources: Sequence[str], observer: str,
        detail: Mapping[str, Any] | None = None,
    ) -> Row:
        """What was touched, according to `observer`.

        The agent naming itself here produces an untrusted row, which `authorised_effect`
        will refuse to treat as evidence. That is the point.
        """
        payload: dict[str, Any] = {"resources": list(resources)}
        if detail is not None:
            payload["detail"] = dict(detail)
        return self._append(correlation_id, "effect", observer, payload)

    def record_outcome(
        self, correlation_id: str, *, ground_truth: bool, confirmed_by: str,
        question: str = "", confidence: float | None = None,
    ) -> Row:
        """Ground truth. Refused unless the confirmer is a configured trusted source.

        The attempt is APPENDED FIRST and then refused, so an agent trying to confirm its own
        outcome leaves a permanent untrusted row rather than vanishing. The row is evidence that
        a claim was made; it is not evidence the claim is true, and `labelled()` will not return
        it.
        """
        row = self._append(
            correlation_id, "outcome", confirmed_by,
            {
                "ground_truth": bool(ground_truth), "question": question,
                "confidence": confidence,
            },
        )
        if not row.trusted:
            raise EvidenceError(
                f"{confirmed_by!r} is not a configured trusted source; an unattributed outcome "
                "is not a label and will not reach calibration"
            )
        return row

    # ------------------------------------------------------------------ reading

    def head_hash(self) -> str:
        row = self._conn.execute("SELECT row_hash FROM evidence ORDER BY id DESC LIMIT 1").fetchone()
        return str(row["row_hash"]) if row else GENESIS

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[Row]:
        out = []
        for r in self._conn.execute(sql, params):
            out.append(
                Row(
                    id=int(r["id"]), correlation_id=str(r["correlation_id"]), kind=str(r["kind"]),
                    source=str(r["source"]), trust=str(r["trust"]),
                    payload=json.loads(r["payload_json"]), recorded_at=str(r["recorded_at"]),
                    prev_hash=str(r["prev_hash"]), row_hash=str(r["row_hash"]),
                )
            )
        return out

    def all_rows(self) -> list[Row]:
        return self._rows("SELECT * FROM evidence ORDER BY id")

    def chain(self, correlation_id: str) -> list[Row]:
        """Every row for one decision, in order: the reconstructable chain."""
        return self._rows(
            "SELECT * FROM evidence WHERE correlation_id = ? ORDER BY id", (correlation_id,)
        )

    def verify_chain(self) -> tuple[bool, str]:
        """Recompute every hash. Returns (ok, reason)."""
        prev = GENESIS
        for row in self.all_rows():
            if row.prev_hash != prev:
                return False, f"row {row.id} does not follow its predecessor"
            expected = _row_hash(
                prev, row.kind, row.correlation_id, row.source, row.trust, _canonical(row.payload)
            )
            if expected != row.row_hash:
                return False, f"row {row.id} has been altered since it was written"
            prev = row.row_hash
        return True, f"{len(self.all_rows())} row(s) verified"

    def authorised_effect(self, correlation_id: str) -> tuple[list[str] | None, str]:
        """The observed resources IF a trusted observer reported them.

        Returns (resources, reason). `None` means no trusted observation exists -- which is
        UNKNOWN, not "nothing was touched".
        """
        effects = [r for r in self.chain(correlation_id) if r.kind == "effect"]
        if not effects:
            return None, "no effect was observed at all"
        trusted = [r for r in effects if r.trusted]
        if not trusted:
            sources = ", ".join(sorted({r.source for r in effects}))
            return None, f"only untrusted observers reported an effect ({sources})"
        return list(trusted[-1].payload.get("resources") or []), f"observed by {trusted[-1].source}"

    def approved(self, correlation_id: str) -> bool:
        """Whether a trusted approver granted this decision."""
        return any(
            r.kind == "approval" and r.trusted and r.payload.get("granted") is True
            for r in self.chain(correlation_id)
        )

    def labelled(self, question: str | None = None) -> list[Row]:
        """Outcome rows from trusted confirmers. The only admissible labels."""
        rows = [r for r in self.all_rows() if r.kind == "outcome" and r.trusted]
        if question is not None:
            rows = [r for r in rows if r.payload.get("question") == question]
        latest: dict[str, Row] = {}
        for row in rows:
            latest[row.correlation_id] = row
        return list(latest.values())
