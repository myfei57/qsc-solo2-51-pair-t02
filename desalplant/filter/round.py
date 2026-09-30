"""A diagnostic round that binds its samples to one calibration version."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from .resistance import BedSample


@dataclass
class DiagnosticRound:
    """Open sample set that closes onto an immutable verdict."""

    round_id: int
    baseline_version: int
    status: str  # "open" | "closed"
    samples: list[BedSample] = field(default_factory=list)
    opened_tick: int = 0
    closed_tick: int | None = None

    def open(self) -> bool:
        """Whether the round still accepts samples."""
        return self.status == "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "baseline_version": self.baseline_version,
            "status": self.status,
            "samples": [
                {"flow": sample.flow, "drop": sample.drop, "restored": sample.restored}
                for sample in self.samples
            ],
            "opened_tick": self.opened_tick,
            "closed_tick": self.closed_tick,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "DiagnosticRound":
        samples = [
            BedSample(
                flow=float(sample["flow"]),
                drop=float(sample["drop"]),
                restored=bool(sample.get("restored", False)),
            )
            for sample in row.get("samples", [])
        ]
        return cls(
            round_id=int(row["round_id"]),
            baseline_version=int(row["baseline_version"]),
            status=str(row["status"]),
            samples=samples,
            opened_tick=int(row.get("opened_tick", 0)),
            closed_tick=(
                int(row["closed_tick"]) if row.get("closed_tick") is not None else None
            ),
        )
