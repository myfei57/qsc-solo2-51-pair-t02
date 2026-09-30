"""Immutable verdict records committed when a round closes.

A verdict is the single source of truth for both the operator console and the
external data feed: both read projections of the *same* stored record. Records
are append-only and chained, each one carrying the SHA-256 digest of its
predecessor, so a later calibration version (or any edit) can detectably alter
neither the conclusion nor the version it was issued under.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from ...store.codec import canonical
from .models import BedSample, ResistanceCurve, RoundKind, Verdict


@dataclass(frozen=True)
class VerdictRecord:
    """The immutable conclusion of one closed round."""

    round_no: int
    kind: RoundKind
    verdict: Verdict
    baseline_version: str
    opened_tick: int
    closed_tick: int
    ratio: float | None
    curve: ResistanceCurve | None
    reason_code: str
    reason_detail: str
    prev_hash: str
    samples: tuple[BedSample, ...] = field(default_factory=tuple)
    record_hash: str = ""

    def payload(self) -> dict[str, Any]:
        """Everything the hash covers, without the hash fields themselves."""
        return {
            "round_no": self.round_no,
            "kind": self.kind.value,
            "verdict": self.verdict.value,
            "baseline_version": self.baseline_version,
            "opened_tick": self.opened_tick,
            "closed_tick": self.closed_tick,
            "ratio": self.ratio,
            "curve": None if self.curve is None else self.curve.to_dict(),
            "reason_code": self.reason_code,
            "reason_detail": self.reason_detail,
            "prev_hash": self.prev_hash,
            "samples": [sample.to_dict() for sample in self.samples],
        }

    def to_dict(self) -> dict[str, Any]:
        """Console/external projection; equal for both channels."""
        payload = self.payload()
        payload["record_hash"] = self.record_hash
        return payload

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "VerdictRecord":
        """Rebuild a stored record."""
        curve_row = row.get("curve")
        return cls(
            round_no=int(row["round_no"]),
            kind=RoundKind(str(row["kind"])),
            verdict=Verdict(str(row["verdict"])),
            baseline_version=str(row["baseline_version"]),
            opened_tick=int(row.get("opened_tick", 0)),
            closed_tick=int(row.get("closed_tick", 0)),
            ratio=None if row.get("ratio") is None else float(row["ratio"]),
            curve=None if curve_row is None else ResistanceCurve.from_dict(curve_row),
            reason_code=str(row.get("reason_code", "")),
            reason_detail=str(row.get("reason_detail", "")),
            prev_hash=str(row.get("prev_hash", "")),
            samples=tuple(BedSample.from_dict(item) for item in row.get("samples", ())),
            record_hash=str(row.get("record_hash", "")),
        )


def hash_payload(record: VerdictRecord) -> str:
    """Digest of a record's payload, chained on its predecessor's digest."""
    return hashlib.sha256(canonical(record.payload()).encode("utf-8")).hexdigest()


def verify_chain(records: list[VerdictRecord] | tuple[VerdictRecord, ...]) -> bool:
    """True when every record's stored hash and prev-hash link is intact."""
    previous = ""
    for record in records:
        if record.record_hash != hash_payload(record):
            return False
        if record.prev_hash != previous:
            return False
        previous = record.record_hash
    return True
